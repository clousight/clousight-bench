"""Artifact-level tests for the committed viewer build (resources/viewer/dist).

The viewer is a React/Vite app whose build output is committed and shipped in
the wheel. Component names do not survive minification, so these tests pin the
contract at the artifact level instead:

- OFFLINE-FIRST: no ``http(s)://`` substrings anywhere in dist. The only
  allowance is the W3C SVG namespace identifier inside ``.svg`` files
  (``xmlns="http://www.w3.org/2000/svg"``): it is a namespace *name* the XML
  parser matches byte-for-byte, never a fetched URL, and SVG documents do not
  render without it. JS chunks get the same treatment at build time from the
  vite offline-guard plugin (which splits the namespace literals instead).
- STRICT CSP compatibility: the served page has ``script-src 'self'``, so
  dist/index.html must contain NOTHING inline — every ``<script>`` is an
  empty-bodied ``src=`` tag, and there are no ``<style>`` tags or ``on*=``
  handler attributes.
- XSS discipline: ``dangerouslySetInnerHTML`` is absent from web/src (source
  ONLY: react-dom's production bundle legitimately contains the literal, so
  dist is deliberately not grepped for it).
- i18n discipline: every key referenced as a ``t('...')`` / ``t("...")``
  literal in web/src exists in BOTH locale dictionaries.
- size sanity: the gzipped dist total stays under 2.5 MB.
"""

from __future__ import annotations

import ast
import gzip
import json
import re
from importlib.resources import files as resource_files
from pathlib import Path

try:  # Traversable moved to importlib.resources.abc in 3.11; it lives in importlib.abc on 3.10
    from importlib.resources.abc import Traversable
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    from importlib.abc import Traversable

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WEB_SRC = _REPO_ROOT / "web" / "src"
_I18N_DIR = _WEB_SRC / "i18n"

#: Namespace *identifiers* (matched byte-for-byte by the XML parser, never
#: fetched) that are unavoidable in standalone SVG documents.
_SVG_NAMESPACE_PREFIX = "http://www.w3.org/"

#: Bundled third-party licence texts that must ship byte-for-byte verbatim by
#: the terms of their own licence (OFL-1.1 requires the IBM Plex Mono licence
#: to travel unmodified with the font). Their one `http://` substring is the
#: licensor's own FAQ pointer, not something this repo authored or fetches —
#: exempt from the offline rewrite rather than an exception to offline-first.
#: The viewer never requests these; they are documents sitting in the bundle.
_VERBATIM_LICENCE_FILES = {"IBM-Plex-Mono-LICENSE.txt"}


def _dist_root() -> Traversable:
    return resource_files("clousight_bench.resources").joinpath("viewer").joinpath("dist")


def _walk_dist(node: Traversable, prefix: str = "") -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for child in node.iterdir():
        name = f"{prefix}{child.name}"
        if child.is_dir():
            out.extend(_walk_dist(child, prefix=f"{name}/"))
        else:
            out.append((name, child.read_bytes()))
    return sorted(out)


@pytest.fixture(scope="module")
def dist_files() -> list[tuple[str, bytes]]:
    entries = _walk_dist(_dist_root())
    assert entries, "committed dist is empty — run `npm run build` in web/"
    return entries


@pytest.fixture(scope="module")
def index_html(dist_files: list[tuple[str, bytes]]) -> str:
    by_name = dict(dist_files)
    assert "index.html" in by_name, "dist/index.html missing"
    return by_name["index.html"].decode("utf-8")


# ---------------------------------------------------------------------------
# Offline-first: no external URLs in any dist file
# ---------------------------------------------------------------------------


def test_no_external_urls_in_dist(dist_files: list[tuple[str, bytes]]) -> None:
    for name, data in dist_files:
        if name in _VERBATIM_LICENCE_FILES:
            continue  # ships byte-for-byte by licence obligation; see comment above
        text = data.decode("utf-8", errors="replace")
        for match in re.finditer(r"https?://[^\s\"'`<)]*", text):
            if name.endswith(".svg") and match.group(0).startswith(_SVG_NAMESPACE_PREFIX):
                continue  # namespace identifier, not a network request
            pytest.fail(f"external URL in dist/{name}: {match.group(0)!r}")


def test_dist_gzipped_size_under_budget(dist_files: list[tuple[str, bytes]]) -> None:
    total = sum(len(gzip.compress(data, compresslevel=6)) for _, data in dist_files)
    budget = int(2.5 * 1024 * 1024)
    assert total < budget, f"gzipped dist total {total} bytes exceeds budget {budget}"


# ---------------------------------------------------------------------------
# Strict-CSP index.html: src-only scripts, nothing inline
# ---------------------------------------------------------------------------


def test_index_scripts_are_src_only(index_html: str) -> None:
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", index_html, re.DOTALL)
    assert scripts, "dist/index.html has no <script> tag at all"
    for attrs, body in scripts:
        assert re.search(r"\bsrc\s*=", attrs), f"inline <script{attrs}> violates script-src 'self'"
        assert not body.strip(), f"<script{attrs}> has an inline body"


def test_index_has_no_inline_style_or_handlers(index_html: str) -> None:
    assert "<style" not in index_html
    assert not re.search(r"\son\w+\s*=", index_html), "inline on*= handler attribute"
    assert not re.search(r"\sstyle\s*=", index_html), "inline style= attribute"


def test_index_references_favicon(index_html: str, dist_files: list[tuple[str, bytes]]) -> None:
    assert "favicon.svg" in index_html
    assert any(name == "favicon.svg" for name, _ in dist_files), "favicon.svg missing from dist"


# ---------------------------------------------------------------------------
# ECharts: bundled (offline), and in exactly one JS chunk
# ---------------------------------------------------------------------------


def test_echarts_bundled_in_exactly_one_chunk(dist_files: list[tuple[str, bytes]]) -> None:
    # "_echarts_instance_" is the DOM attribute key echarts' init/getInstanceByDom
    # writes at runtime — a functional string literal that survives minification,
    # unlike component names. Requiring exactly one hit also guards against the
    # dependency being duplicated across chunks.
    marker = b"_echarts_instance_"
    chunks = [name for name, data in dist_files if name.endswith(".js") and marker in data]
    assert len(chunks) == 1, f"expected the echarts runtime in exactly one JS chunk, got {chunks}"


# ---------------------------------------------------------------------------
# Source discipline: no dangerous sinks, i18n keys resolve
# ---------------------------------------------------------------------------


def _web_src_files() -> list[Path]:
    paths = [p for p in _WEB_SRC.rglob("*") if p.suffix in {".ts", ".tsx"} and p.is_file()]
    assert paths, f"no TypeScript sources under {_WEB_SRC}"
    return paths


def test_no_dangerously_set_inner_html_in_source() -> None:
    # web/src ONLY: the react-dom production chunk in dist legitimately
    # contains this literal, so the committed bundle is out of scope here.
    for path in _web_src_files():
        assert "dangerouslySetInnerHTML" not in path.read_text(encoding="utf-8"), (
            f"dangerouslySetInnerHTML found in {path}"
        )


#: t('key') / t("key") call sites; (?<![\w$]) keeps identifiers ending in "t"
#: (split(, parseInt(, ...) out of scope.
_T_CALL_RE = re.compile(r"(?<![\w$])t\(\s*(['\"])([^'\"]+)\1")

#: ``tracks.ts``'s stream attribute, as declared there.
_STREAM_ATTR_RE = re.compile(r'const STREAM_ATTR = "([^"]+)"')

#: Any ``csbench.*`` attribute key written from Python, for the failure message.
_CSBENCH_ATTR_RE = re.compile(r'"(csbench\.[\w.]+)"')


def test_track_stream_attribute_matches_the_emitter() -> None:
    """The viewer's lane grouping must key on an attribute the emitter writes.

    ``tracks.ts`` read ``csbench.stream`` while
    ``suites/_tpc_official/trace.py`` wrote ``csbench.stream_id``, so the
    declared-stream path was dead for the only workload in this repo that
    declares streams: every throughput query fell through to ``parentId``
    packing and the browser drew ten anonymous lanes instead of three named
    ones. The TypeScript suite could not catch it because its own fixtures used
    the invented key — the bug and the test agreed with each other.

    Nothing inside one language can check this. Here the constant is read out
    of the TypeScript and looked for in the Python that produces the traces,
    which is the only place the two spellings meet.
    """
    tracks = (_WEB_SRC / "lib" / "tracks.ts").read_text(encoding="utf-8")
    match = _STREAM_ATTR_RE.search(tracks)
    assert match is not None, (
        "tracks.ts no longer declares STREAM_ATTR as a one-line string literal, so this test"
        " cannot see which attribute the lanes group by — keep it greppable or update the regex"
    )
    attr = match.group(1)

    emitted: set[str] = set()
    writers: list[str] = []
    for path in sorted((_REPO_ROOT / "src" / "clousight_bench").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        emitted.update(_CSBENCH_ATTR_RE.findall(text))
        if f'"{attr}"' in text:
            writers.append(path.relative_to(_REPO_ROOT).as_posix())
    # Without this the test would pass on an empty scan — exactly what it would
    # do if the emitters moved and the glob stopped finding them.
    assert emitted, "no csbench.* span attribute found under src/clousight_bench (did the emitters move?)"
    assert writers, (
        f"tracks.ts groups lanes by {attr!r}, which no Python source writes."
        f" Attributes actually emitted: {sorted(emitted)}"
    )


#: One ``name: "--token",`` entry of ``palette.ts``'s KIND_SLOTS table.
_KIND_SLOT_RE = re.compile(r'^\s*(\w+):\s*"(--[\w-]+)",\s*$', re.MULTILINE)

#: ``palette.test.ts``'s hand-copied list of the kinds the backend can emit.
_KINDS_FROM_BACKEND_RE = re.compile(r"const KINDS_FROM_BACKEND = \[([^\]]*)\]")


def _v3_kind_returns() -> set[str]:
    """Every string ``viewer/data.py::_v3_kind`` can return, read from its AST.

    Parsed rather than grepped so a ``return`` that stops being a plain string
    literal — a variable, an f-string, a lookup — fails loudly here instead of
    quietly narrowing what this test believes the backend produces.
    """
    source = (_REPO_ROOT / "src" / "clousight_bench" / "viewer" / "data.py").read_text(encoding="utf-8")
    functions = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "_v3_kind"
    ]
    assert len(functions) == 1, (
        f"expected exactly one _v3_kind in viewer/data.py, found {len(functions)} —"
        " the viewer's span kinds are derived there and this pin reads it by name"
    )
    kinds: set[str] = set()
    for node in ast.walk(functions[0]):
        if not isinstance(node, ast.Return):
            continue
        assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str), (
            "_v3_kind has a return that is not a plain string literal"
            f" (line {node.lineno}); this cross-language pin can no longer enumerate its kinds"
        )
        kinds.add(node.value.value)
    return kinds


def test_kind_slots_match_the_backend() -> None:
    """The palette's kind table must be exactly what ``_v3_kind`` can return.

    A kind with no slot paints in the fallback, so colour silently stops
    carrying identity; a slot for a kind that no longer exists is how
    ``db_query`` and ``stage`` survived a rename with every bar the same blue.

    The TypeScript suite asserts both directions already — against
    ``KINDS_FROM_BACKEND``, a list hand-copied out of Python inside
    ``palette.test.ts``. That list is not evidence: the day ``_v3_kind`` grows
    a case, the list and the palette stay in step with each other and out of
    step with the backend, and the suite goes green. It is the same shape as
    the defect this branch already shipped — ``tracks.ts`` grouped lanes by
    ``csbench.stream`` while the emitter wrote ``csbench.stream_id``, and the
    unit tests used the invented key too, so nothing could see it for seven
    tasks (see ``test_track_stream_attribute_matches_the_emitter``).

    Nothing inside one language can check this. Here the Python function is
    parsed for its returns and held against both the palette table and the
    hand-copied list, which is the only place the two spellings meet.
    """
    backend = _v3_kind_returns()
    assert backend, "no string returns found in _v3_kind — has it been rewritten?"

    palette = (_WEB_SRC / "charts" / "palette.ts").read_text(encoding="utf-8")
    start = palette.find("export const KIND_SLOTS")
    assert start != -1, "palette.ts no longer declares KIND_SLOTS — update this pin with it"
    table = palette[start : palette.index("};", start)]
    slots = {match.group(1) for match in _KIND_SLOT_RE.finditer(table)}
    assert slots, 'KIND_SLOTS is no longer a literal `kind: "--token",` table; this pin cannot read it'

    assert slots == backend, (
        f"KIND_SLOTS and viewer/data.py::_v3_kind disagree — slots without a kind:"
        f" {sorted(slots - backend)}; kinds without a slot: {sorted(backend - slots)}"
    )

    spec = (_WEB_SRC / "charts" / "palette.test.ts").read_text(encoding="utf-8")
    match = _KINDS_FROM_BACKEND_RE.search(spec)
    assert match is not None, (
        "palette.test.ts no longer declares KINDS_FROM_BACKEND as a one-line array literal,"
        " so the list it asserts the palette against is unpinned — keep it greppable"
    )
    copied = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert copied == backend, (
        f"palette.test.ts's KINDS_FROM_BACKEND is stale — it lists {sorted(copied)},"
        f" _v3_kind returns {sorted(backend)}"
    )


def test_all_t_referenced_keys_exist_in_both_locales() -> None:
    en = json.loads((_I18N_DIR / "en.json").read_text(encoding="utf-8"))
    zh = json.loads((_I18N_DIR / "zh.json").read_text(encoding="utf-8"))
    referenced: dict[str, list[str]] = {}
    for path in _web_src_files():
        for match in _T_CALL_RE.finditer(path.read_text(encoding="utf-8")):
            referenced.setdefault(match.group(2), []).append(path.name)
    assert len(referenced) >= 20, (
        f"only {len(referenced)} t('...') literals found — views must draw their strings from i18n"
    )
    for key, sources in sorted(referenced.items()):
        assert key in en, f"t({key!r}) in {sources} missing from en.json"
        assert key in zh, f"t({key!r}) in {sources} missing from zh.json"


def test_stage_timings_are_formatted_as_milliseconds() -> None:
    """``run.stage_timings`` is in MILLISECONDS (``orchestrator._ms``).

    Handing those numbers to ``fmtDur(seconds)`` renders a 496 ms teardown as
    "8.3m" — a 1000x lie in the shipped viewer. The stage card must go through
    the millisecond formatter instead.
    """
    fmt = (_WEB_SRC / "lib" / "format.ts").read_text(encoding="utf-8")
    assert "export function fmtDurMs(" in fmt, "format.ts must expose a millisecond duration formatter"

    # Scanned across the whole source tree rather than one named file: the
    # stage card has already moved once (views/RecordDetail.tsx ->
    # components/Lifecycle.tsx), and a test pinned to a path stops guarding the
    # invariant the moment the code is reorganised.
    sources = [path for path in _web_src_files() if path.suffix in {".ts", ".tsx"}]
    users = [path for path in sources if "stage_timings" in path.read_text(encoding="utf-8")]
    assert users, "no source reads stage_timings — has the field been renamed?"
    assert any("fmtDurMs(" in path.read_text(encoding="utf-8") for path in sources), (
        "no source formats a stage duration with fmtDurMs — the stage card must use it"
    )
    for path in users:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "stage_timings" in line or ("timings[" in line and "fmtDur(" in line):
                assert "fmtDur(" not in line or "fmtDurMs(" in line, (
                    f"stage timings must not be passed to the seconds formatter ({path.name}): {line.strip()}"
                )


#: The route switch's trace arm, with whatever props it passes.
_TRACE_ROUTE_RE = re.compile(r'case "trace":.*?<TraceView([^/>]*)/>', re.DOTALL)


def test_trace_view_is_keyed_on_the_run() -> None:
    """A trace -> trace navigation must not carry the old run's selection over.

    ``TraceView`` holds a ``Selection``: absolute epoch seconds and track ids
    belonging to the trace it was built for. Rendered without a ``key``, React
    reuses the instance across a run change, and neither number means anything
    in the new run — every pane read "No spans in this selection" with nothing
    on screen to explain it.

    This is a source-text guard, and it is worth being clear about what it can
    see: a React key's effect is that the component instance is torn down and
    remounted, which no static render can observe, so what is checked here is
    that the key is passed at all. It fails if the prop is dropped, which is
    the regression it exists for.
    """
    app = (_WEB_SRC / "App.tsx").read_text(encoding="utf-8")
    match = _TRACE_ROUTE_RE.search(app)
    assert match is not None, (
        'App.tsx no longer renders <TraceView .../> from a `case "trace":` arm, so this test'
        " cannot see how it is mounted — update the regex along with the routing"
    )
    assert "key=" in match.group(1), (
        "<TraceView> must be keyed on the run id, or React reuses one instance — and its"
        " selection, in the previous trace's epoch seconds and track ids — across a"
        f" trace-to-trace navigation. Props found: {match.group(1).strip()!r}"
    )


def test_viewer_bundles_its_own_monospace(dist_files: list[tuple[str, bytes]]) -> None:
    """Identity lives in the numbers, so the mono is bundled rather than borrowed.

    It must be Latin-only and content-hashed into assets/: only that prefix gets
    the immutable cache header from viewer/server.py::_cache_for, and a CJK face
    would cost megabytes against a 908 KB budget for the whole viewer.
    """
    fonts = [name for name, _ in dist_files if name.endswith(".woff2")]
    assert fonts, "no bundled font in dist — the viewer must not depend on a system mono"
    for name in fonts:
        assert name.startswith("assets/"), (
            f"{name} is outside assets/, so it is served no-store instead of immutable"
        )

    css = (_WEB_SRC / "index.css").read_text(encoding="utf-8")
    assert "@font-face" in css, "index.css must declare the bundled face"
    assert "IBM Plex Mono" in css, "index.css must name the bundled family"

    licence = [name for name, _ in dist_files if "LICENSE" in name.upper() and "PLEX" in name.upper()]
    assert licence, "OFL-1.1 requires the licence text to ship with the font"


def test_bundled_font_stays_within_a_latin_subset_budget(dist_files: list[tuple[str, bytes]]) -> None:
    """Two Latin weights is the whole allowance. A third weight, or any CJK face,
    is a different decision and must not arrive by accident."""
    fonts = [(name, data) for name, data in dist_files if name.endswith(".woff2")]
    assert len(fonts) <= 2, f"more than two bundled weights: {[n for n, _ in fonts]}"
    total = sum(len(data) for _, data in fonts)
    assert total < 80 * 1024, f"bundled fonts total {total} bytes — a Latin subset is ~15 KB per weight"


#: Tokens that dress the interface rather than carry a measurement. Chart and
#: status tokens are deliberately absent: those ARE the data channel.
_CHROME_TOKENS = (
    "background",
    "foreground",
    "card",
    "card-foreground",
    "primary",
    "primary-foreground",
    "secondary",
    "secondary-foreground",
    "muted",
    "muted-foreground",
    "accent",
    "accent-foreground",
    "border",
    "input",
    "ring",
)

_OKLCH_RE = re.compile(r"oklch\(\s*[\d.]+%?\s+([\d.]+)\s")

#: The only non-literal form a chrome token is allowed to take: a reference to
#: another custom property, captured so the target name can be resolved and
#: checked in its own right (see ``test_chrome_tokens_carry_no_hue``) rather
#: than merely pattern-matched and waved through.
_ALIAS_RE = re.compile(r"^var\(--([\w-]+)\)$")


def _declared_values(css: str, name: str) -> list[str]:
    """Every ``--name: value;`` declaration of a custom property, across every
    scope (``:root``, ``.dark``, ...). The same shape used to find a chrome
    token's own declaration, reused to resolve one level of ``var()`` alias."""
    return [m.group(1).strip() for m in re.finditer(rf"^\s*--{re.escape(name)}:\s*(.+?);", css, re.MULTILINE)]


def test_chrome_tokens_carry_no_hue() -> None:
    """Colour is the data channel; chrome must not compete with it.

    A hue in the chrome is how an interface starts arguing with its own charts —
    the four categorical slots were validated for separation against each other,
    not against a tinted header. Status colours and chart slots are excluded
    because carrying meaning is exactly their job.

    A chrome token may alias another custom property with ``var(--other)``,
    but the alias is only as clean as what it points at: this resolves ONE
    level of indirection and applies the same chroma test to the target's own
    declaration, rather than trusting that the shape ``var(...)`` implies a
    reviewed value. Aliasing a chrome token straight to a chart or status slot
    (e.g. ``--card: var(--chart-1)``) is exactly the cheap way to smuggle a hue
    into the chrome, and it is caught here now. Deliberately NOT chased further
    than one level: a target that is itself an alias fails loudly instead of
    being silently accepted, since following chains needs real recursion for a
    case nothing in this file currently needs.
    """
    css = (_WEB_SRC / "index.css").read_text(encoding="utf-8")
    offenders: list[str] = []
    matched: set[str] = set()
    for token in _CHROME_TOKENS:
        for value in _declared_values(css, token):
            matched.add(token)
            alias = _ALIAS_RE.match(value)
            if alias is None:
                chroma = _OKLCH_RE.match(value)
                if chroma is None:
                    offenders.append(f"--{token}: {value} (neither an oklch() literal nor a var() alias)")
                elif float(chroma.group(1)) > 0.02:
                    offenders.append(f"--{token}: {value}")
                continue
            target = alias.group(1)
            target_values = _declared_values(css, target)
            if not target_values:
                offenders.append(f"--{token}: {value} -> --{target} is not declared anywhere in index.css")
                continue
            for target_value in target_values:
                if _ALIAS_RE.match(target_value):
                    offenders.append(
                        f"--{token}: {value} -> --{target}: {target_value} is itself an alias; "
                        "this test resolves one level of var() only"
                    )
                    continue
                chroma = _OKLCH_RE.match(target_value)
                if chroma is None:
                    offenders.append(
                        f"--{token}: {value} -> --{target}: {target_value} is not an oklch() literal"
                    )
                elif float(chroma.group(1)) > 0.02:
                    offenders.append(f"--{token}: {value} -> --{target}: {target_value}")
    assert not offenders, "chrome tokens with a hue: " + ", ".join(offenders)
    # A ratchet that never sees its token rename/deletion is not a ratchet: it
    # would silently pass with zero matches while guarding nothing.
    unmatched = [token for token in _CHROME_TOKENS if token not in matched]
    assert not unmatched, f"chrome tokens never declared in index.css (renamed or removed?): {unmatched}"


def test_chrome_components_do_not_reuse_chart_series_slots() -> None:
    """A chip or a button reaching for a chart slot re-uses a series colour as
    decoration, which silently breaks the rule that a slot belongs to one series
    by identity.

    `status-*` is a different channel from `chart-*` and is deliberately NOT
    banned here: a chrome element (like the header's live-run count) can be a
    genuine status indicator, and stripping its colour to satisfy this test
    would violate the rule this whole task exists for — colour carries data
    AND status, chrome just must not invent a hue of its own. Only the four
    categorical chart slots, which are validated for separation against each
    other as an identity-by-series contract, are off limits to decoration.

    The needle is the bare substring "chart-", not "chart-1".."chart-4": the
    semantic aliases (`--chart-llm`, `--chart-tool`, `--chart-db`,
    `--chart-stage`) resolve to the same four slots and are the *more* natural
    thing to reach for since they read by meaning, and `--chart-grid` /
    `--chart-axis` are the same series-adjacent surface. A narrower needle
    would leave all six reachable by name. Left consciously unguarded: a
    chrome element reaching for a `status-*` colour with no real status to
    report. That is a judgement call ("is this genuinely status?") no grep
    can make, so it stays a human-review concern rather than a test.
    """
    chrome = [
        _WEB_SRC / "components" / "ui" / "badge.tsx",
        _WEB_SRC / "components" / "ui" / "button.tsx",
        _WEB_SRC / "components" / "ui" / "tabs.tsx",
        _WEB_SRC / "components" / "ui" / "input.tsx",
        _WEB_SRC / "components" / "Header.tsx",
    ]
    for path in chrome:
        text = path.read_text(encoding="utf-8")
        assert "chart-" not in text, f"{path.name} references a chart-* slot or alias"


def test_no_rounded_card_surface_in_source() -> None:
    """The boxed card is the shadcn look, and it is what the redesign removed.

    Kept as a test rather than a convention because the primitive is one npx
    command away from coming back, and it would come back one view at a time.
    """
    card = _WEB_SRC / "components" / "ui" / "card.tsx"
    assert not card.exists(), "the card primitive is back — sections replaced it deliberately"
    for path in _web_src_files():
        if path.suffix not in {".ts", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "components/ui/card" not in text, f"{path.name} imports the deleted card primitive"
        assert "<Card" not in text, f"{path.name} still renders a Card"


# ---------------------------------------------------------------------------
# Visual-system ratchets: the two rules that regressed repeatedly
# ---------------------------------------------------------------------------

#: `tabular-nums` sites that inherit `font-mono` from an ancestor instead of
#: declaring it. A regex cannot resolve ancestry, so each one is named here
#: together with the ancestor that supplies the face. When a line moves this
#: fails and someone re-confirms the ancestor is still there, which is the
#: point — the alternative is a blanket skip that stops guarding anything.
_MONO_BY_INHERITANCE = {
    "features/live/LogStream.tsx": "the scroll pane in the same component carries font-mono",
}


def test_every_number_is_monospace() -> None:
    """Rule 1 of the visual system: one monospace carries every number.

    This regressed three times. `Glossed.tsx` and `Lifecycle.tsx` were in no
    task's file list; then a fix applied to `MetricValue` was believed to be
    shared and turned out to reach exactly one view, leaving thirteen spans
    across eight files rendering numbers in the system sans stack. Nothing
    swept for it, so nothing caught it — each time a human had to render the
    app and look.

    `tabular-nums` is the marker this codebase puts on number-bearing spans, so
    it is what we key on. A number rendered without it is out of reach here;
    that is a known limit, not an oversight.
    """
    offenders: list[str] = []
    seen_exempt: set[str] = set()
    for path in _web_src_files():
        if path.suffix != ".tsx":
            continue
        rel = path.relative_to(_WEB_SRC).as_posix()
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "tabular-nums" not in line or "font-mono" in line:
                continue
            if rel in _MONO_BY_INHERITANCE:
                assert "font-mono" in text, (
                    f"{rel} is exempt because {_MONO_BY_INHERITANCE[rel]}, "
                    "but the file no longer declares font-mono anywhere"
                )
                seen_exempt.add(rel)
                continue
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "tabular-nums without font-mono — numbers render in the sans stack at: " + ", ".join(offenders)
    )
    stale = set(_MONO_BY_INHERITANCE) - seen_exempt
    assert not stale, f"exemptions that no longer match anything (remove them?): {sorted(stale)}"


def test_section_bodies_stay_on_the_rail() -> None:
    """Section content sits on the same left edge as its `SectionTitle`.

    `Card` supplied `px-4` and `Section` supplies none, so every migrated view
    had a chance to push its content 16px off the rail, and three of them took
    it. Four fix rounds across three tasks went into converging on this; the
    cost of losing it again is that the sections which follow the rule start to
    look like the mistake.
    """
    offenders: list[str] = []
    for path in _web_src_files():
        if path.suffix != ".tsx":
            continue
        rel = path.relative_to(_WEB_SRC).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "<SectionBody" not in line:
                continue
            if re.search(r'className="[^"]*\b(px|pl|pr)-', line):
                offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "SectionBody with horizontal padding — content is off the left rail at: " + ", ".join(offenders)
    )


#: Any ``api/...`` string literal, in any of JS's three quote characters.
_API_LITERAL = re.compile(r"""["'`](api/[^"'`]*)["'`]""")

#: ``${...}`` inside a template literal, normalised away before comparison so
#: the run id's spelling is not part of the contract.
_TEMPLATE_HOLE = re.compile(r"\$\{[^}]*\}")

#: The only endpoint the timeline may fetch. It returns spans.
_TRAJECTORY_ENDPOINT = "api/record/{}/trajectory"

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//.*$", re.MULTILINE)


def _code_only(text: str) -> str:
    """Drop comments so a prose mention cannot pass for a fetch, or fail as one.

    These files are documented in prose that names the very things the test
    forbids — the point of the invariant is what the code does.
    """
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def test_selection_never_reaches_a_measurement() -> None:
    """Selecting a sub-range moves the observation, never the verdict.

    Measurements come from an `Evaluator`, offline, over sealed evidence, after
    the run ends — they are what `record_digest` covers. A number that grew as
    a range was dragged would imply it had been measured progressively, which
    is the one thing this tool must never imply. The lifecycle enforces it in
    `task.score()`'s signature; this keeps the interface honest about it.

    So the invariant is narrow and checkable: these files reach for spans and
    nothing else. Every ``api/...`` literal in their *code* — comments are
    stripped first, since the prose here names what the code may not do — is
    extracted and compared against the trajectory endpoint. A second fetch,
    ``api/record/<id>`` for the digest or a scored field off it, fails here no
    matter what else the file happens to say. The version this replaces looked
    for substrings anywhere in the file and skipped its only real needle for
    any file containing the word "trajectory", so that second fetch passed it.
    """
    timeline = _WEB_SRC / "features" / "timeline"
    assert timeline.is_dir(), "the timeline feature directory is missing"
    sources = sorted(timeline.rglob("*.tsx")) + [_WEB_SRC / "features" / "trace" / "TraceView.tsx"]
    endpoints: list[tuple[str, str]] = []
    for path in sources:
        code = _code_only(path.read_text(encoding="utf-8"))
        assert not re.search(r"\bmeasurements\b", code), (
            f"{path.name} reaches for 'measurements': a selection must not be able to recompute a measurement"
        )
        for literal in _API_LITERAL.findall(code):
            endpoint = _TEMPLATE_HOLE.sub("{}", literal)
            endpoints.append((path.name, endpoint))
            assert endpoint == _TRAJECTORY_ENDPOINT, (
                f"{path.name} fetches {literal!r}, not the trajectory: the timeline reads spans,"
                " never a scored record"
            )
    # Without this the test passes on zero matches — which is exactly what it
    # would do if the extraction ever stopped matching, or if the fetch moved
    # into a helper these files no longer name.
    assert ("TraceView.tsx", _TRAJECTORY_ENDPOINT) in endpoints, (
        f"no {_TRAJECTORY_ENDPOINT!r} literal found in TraceView.tsx; found {endpoints!r}."
        " The extraction is no longer looking at the code that fetches."
    )


#: A `pctOf(` CALL or definition — never the bare name. Three source files
#: explain in prose why this function must not exist, `TraceTree.tsx` twice,
#: and a substring scan would fail on the explanation. That is not a
#: hypothetical: the `dangerouslySetInnerHTML` guard flagged two doc comments
#: that merely quoted it, and a task had to reword prose to satisfy a test.
#: Comments are stripped before this is applied, so the parenthesis is what
#: separates "calls it" from "warns about it".
_PCT_OF_CALL = re.compile(r"\bpctOf\s*\(")


def test_no_pct_of_total_survives() -> None:
    """The one defect the whole trace redesign exists to remove, guarded.

    `pctOf(seconds, t0, totalS)` positioned every bar as a fraction of the
    WHOLE RUN. A fraction of the run cannot change when the reader zooms, so a
    "zoom" built on it can only ever dim spans — which is precisely what
    shipped, and survived two days in a branch with 190 passing tests. Every
    position in the view now goes through `viewport.ts::place()`, against an
    explicit window.

    The function is gone with the lane list that was its last caller. This is
    what keeps it gone: a percentage helper is four lines to write and reads as
    perfectly reasonable in isolation, so it would come back one component at a
    time, exactly as the boxed card did.
    """
    # Self-check, so this cannot pass because the pattern stopped matching:
    # a call is caught, and the same text inside a comment is not.
    sample = "const left = pctOf(row.startS, t0, totalS);"
    assert _PCT_OF_CALL.search(sample), "the extraction no longer recognises a pctOf call"
    assert not _PCT_OF_CALL.search(_code_only(f"// {sample}")), (
        "comments are no longer stripped, so prose explaining pctOf would fail this test"
    )

    offenders: list[str] = []
    for path in _web_src_files():
        rel = path.relative_to(_WEB_SRC).as_posix()
        # The offending LINE, not its number: stripping a block comment
        # collapses it to nothing, so line numbers in the stripped text no
        # longer address the file on disk, and a number that is almost right
        # is worse than the code itself.
        for line in _code_only(path.read_text(encoding="utf-8")).splitlines():
            if _PCT_OF_CALL.search(line):
                offenders.append(f"{rel}: {line.strip()}")
    assert not offenders, (
        "pctOf is back — a bar positioned against the run total cannot be zoomed, only dimmed: "
        + ", ".join(offenders)
    )


def test_no_source_file_contains_a_nul_byte() -> None:
    """A control character in a source file makes it binary to grep — and to reviewers.

    This is not defensive: `TraceTree.tsx` shipped with a literal NUL inside a
    React key (`` `${id}\0lane${n}` ``) for two tasks. It is invisible in an
    editor and harmless to the bundler.

    WHAT IT ACTUALLY BREAKS, replayed against the historical blob rather than
    assumed. `file` reports the source as "data"; the grep family (including
    the one a reviewer runs over the index) classifies it as binary and prints
    "binary file matches" in place of the line, or skips it outright under
    ``-I``. So a reviewer sweeping the tree for ``pctOf`` got no hit from the
    one file that still had it.

    The Python scans in this module are NOT affected, and an earlier version of
    this docstring wrongly said they were. A NUL is valid UTF-8:
    ``Path.read_text()`` returns it and ``"needle" in text`` matches straight
    through it, so ``_web_src_files()`` and every substring guard built on it
    read that file the whole time it was "binary".

    A shell-level blind spot is reason enough to fail. Half of what guards this
    repo is a reviewer's own text search, and a file that has silently dropped
    out of that half is a file nobody is reading. A test that reads BYTES is
    the only thing that can see it.
    """
    offenders: list[str] = []
    for path in sorted(_WEB_SRC.rglob("*")):
        if not path.is_file() or path.suffix in {".woff2"}:
            continue  # fonts are binary by design and grep is not asked to read them
        data = path.read_bytes()
        index = data.find(b"\x00")
        if index != -1:
            offenders.append(f"{path.relative_to(_WEB_SRC).as_posix()} at byte {index}")
    assert not offenders, (
        "NUL byte in source — every text search over this tree now reports the file as binary "
        "and stops showing its lines: " + ", ".join(offenders)
    )


#: A JSX element with no nested element inside its own tag — enough to isolate
#: one component's props from its neighbours' in a render tree.
_JSX_ELEMENT = re.compile(r"<[A-Z][A-Za-z0-9]*\b[^<>]*?/?>", re.DOTALL)

#: ``totalS={...}`` / ``t0={...}`` props, with the expression that feeds them.
#: An axis given as an origin and a width — the shape the lane list takes.
_TOTAL_S_PROP = re.compile(r"\btotalS=\{([^}]*)\}")
_T0_PROP = re.compile(r"\bt0=\{([^}]*)\}")

#: ``view={...}``: an axis given as a `Viewport`, which is the shape every
#: component of the assembled trace view takes. Case-sensitive, so ``onView``
#: (the callback that CHANGES the window) is not mistaken for the window.
_VIEW_PROP = re.compile(r"\bview=\{([^}]*)\}")

#: The tag a matched JSX element opens with.
_JSX_TAG_NAME = re.compile(r"<([A-Z][A-Za-z0-9]*)")

#: Names that mean "the whole run". Feeding one to a `view` prop is the same
#: defect as feeding `totalSeconds()` to `totalS`: the component then draws a
#: fixed fraction of the run and a drag can only dim things. ``bounds`` is on
#: this list and belongs there — it is the run, and `OverviewStrip` takes it
#: under its own name precisely because it draws the run under the window.
_RUN_DOMAIN = ("bounds", "total", "totalSeconds", "fullViewport")

#: Every component of the trace view that positions something in time. All
#: three must be mounted and fed the window, or this test passes on an app
#: that no longer has a zoomable trace view in it.
_TIME_AXIS_COMPONENTS = frozenset({"TraceTree", "SpanDock", "OverviewStrip"})


def test_a_time_axis_is_fed_the_window_and_never_the_run_total() -> None:
    """The one capability the trace view exists to deliver, guarded at the wiring.

    A component that draws a time axis takes an origin and a width, and the
    zoom is entirely a question of *which* origin and width the caller hands
    it: the window's, and the axis moves when the reader drags; the run's, and
    every bar is pinned to the whole run and a drag can only dim things. That
    was the shipped defect, and it is invisible from below — the lanes' own
    tests pass identically either way, because the component cannot tell which
    domain it was given, and a static render at arrival cannot tell either
    (the window starts out equal to the run).

    So the guard has to be here, on the call site, and it covers both shapes an
    axis arrives in. A component given an origin and a width (``t0``/``totalS``)
    must get the window's, never ``total``/``totalSeconds``. A component given
    a whole ``Viewport`` (``view``) must get the window and not ``bounds``,
    which is the same mistake with the same symptom: the rows would be pinned
    to the run while the strip moved a rectangle over them.

    The second half is the assembled view's guard. When the lane list stopped
    being mounted, the ``totalS`` scan correctly found nothing and failed
    LOUDLY rather than passing vacuously, which is what brought it here to be
    re-pointed at the props the tree, the dock and the strip actually carry.
    It was re-pointed rather than relaxed: it now also fails if any of those
    three stops being mounted at all.
    """
    offenders: list[str] = []
    wired: set[str] = set()
    for path in _web_src_files():
        # Tests hand these components literal numbers on purpose — a window of
        # [4,6] out of a 10s run is exactly how the lanes' own test pins the
        # window-relative arithmetic. The invariant is about the app's wiring.
        if path.suffix != ".tsx" or path.name.endswith(".test.tsx"):
            continue
        rel = path.relative_to(_WEB_SRC).as_posix()
        code = _code_only(path.read_text(encoding="utf-8"))
        for element in _JSX_ELEMENT.findall(code):
            tag_match = _JSX_TAG_NAME.match(element)
            tag = "?" if tag_match is None else tag_match.group(1)

            total = _TOTAL_S_PROP.search(element)
            if total is not None:
                wired.add(tag)
                width = total.group(1).strip()
                origin = _T0_PROP.search(element)
                if "spanS(" not in width:
                    offenders.append(f"{rel}: totalS={{{width}}} is not a window width")
                if origin is None or "view" not in origin.group(1):
                    got = "absent" if origin is None else f"t0={{{origin.group(1).strip()}}}"
                    offenders.append(f"{rel}: {got} is not the same window's origin")

            window = _VIEW_PROP.search(element)
            if window is not None:
                wired.add(tag)
                fed = window.group(1).strip()
                if any(name in fed for name in _RUN_DOMAIN):
                    offenders.append(f"{rel}: <{tag} view={{{fed}}}> is the run, not the window")
    assert not offenders, (
        "a time axis is being positioned against the run total, so dragging cannot zoom it: "
        + "; ".join(offenders)
    )
    # Without this the test passes on zero matches, which is what it would do
    # if the props were renamed or the axis moved to another component.
    missing = sorted(_TIME_AXIS_COMPONENTS - wired)
    assert not missing, (
        f"no time-axis prop found on {missing} anywhere in web/src: either they are no longer"
        " mounted, or the extraction is no longer looking at the code that positions a time axis"
    )


#: The element that mounts the span dock, with the class list that positions it.
_DOCK_MOUNT = re.compile(r'data-dock="overlay"(?P<body>[\s\S]{0,800}?)className="(?P<cls>[^"]*)"')


def test_the_span_dock_is_an_overlay_not_a_column() -> None:
    """Selecting a span must not be able to narrow the lane it was selected from.

    The dock used to be a sibling column. On the 1104px content column the
    tree spends a fixed 424px of every row on the name cap and the two
    duration columns, so a 264px dock plus its gap took the lane from 680px to
    404px — 61.6% of the row to 36.6% — and scaled every bar by 0.594 with it.
    The narrowest query mark at the throughput window went 2.56px -> ~1.52px,
    i.e. under the 2px line the acceptance gate is stated in, reached by the
    branch's own drill gesture (a lane mark selects AND drills).

    The fix is positional, so the guard is positional: the dock is drawn OVER
    the tree's right edge, which is what makes the tree's row box independent
    of whether anything is selected. A per-mark ``min-width`` floor would have
    made the numbers pass by drawing a 1.5ms span as 2ms — the encoding lie
    this whole redesign exists to remove — and narrowing the name column while
    the dock is open cannot reach the old widths at all.

    A static render cannot see this: `renderToStaticMarkup` has no box model,
    so a width in pixels does not exist there, and selection is state the
    harness cannot drive anyway. The pixel measurement lives in
    ``web/probe/trace-gate-probe.mjs``; this is the invariant behind it.
    """
    path = _WEB_SRC / "features" / "trace" / "TraceView.tsx"
    assert path.is_file(), f"the trace view moved: {path}"
    code = _code_only(path.read_text(encoding="utf-8"))

    match = _DOCK_MOUNT.search(code)
    assert match is not None, (
        "no data-dock mount site with a className in TraceView.tsx — either the dock moved, "
        "or it lost the attribute this guard identifies it by"
    )
    classes = match.group("cls").split()
    assert "absolute" in classes, (
        "the span dock is not absolutely positioned, so it is taking width from the tree's rows "
        f"and every bar narrows when a span is selected: className={match.group('cls')!r}"
    )
    # The shape it would regress to: a fixed-width flex sibling of the tree.
    assert "shrink-0" not in classes, (
        f"the span dock is laid out as a flex column again: className={match.group('cls')!r}"
    )
    assert "flex-1" not in code.split("<TraceTree")[0][-400:], (
        "the tree is wrapped in a flex-1 box again, which is how the dock took width from it"
    )


def test_the_filter_box_holds_no_copy_of_what_the_reader_typed() -> None:
    """`TraceChrome` draws the query; it must never own one.

    The window moves on every pointermove of a strip drag, so `TraceChrome`
    re-renders continuously. A query held in its own state would be a second
    copy of what the reader typed, and the way that fails is that an unrelated
    re-render blanks the box mid-drag.

    THIS GUARD EXISTS BECAUSE THE COMPONENT TEST CANNOT SEE IT. The vitest
    harness does one render with no effects and no events, so two renders with
    the same prop are two independent FIRST renders — and a
    ``useState(query)`` initialiser runs afresh in each of them, producing the
    right value both times. That was proven rather than argued: the reviewer
    added exactly that state to `TraceChrome` and the entire suite stayed
    green. Only the cruder failure ("not fed by the prop at all") was caught.

    What a re-render does is not observable without a DOM; what IS observable,
    and is what actually decides the behaviour, is that the component declares
    no state at all and hands the input the prop directly.
    """
    path = _WEB_SRC / "features" / "trace" / "TraceChrome.tsx"
    assert path.is_file(), f"the trace chrome moved: {path}"
    code = _code_only(path.read_text(encoding="utf-8"))

    # Non-vacuity, both halves: the component really does take a `query` prop,
    # and the input really is fed by it. Without these the assertion below
    # would pass on a file that had stopped rendering a filter box.
    assert re.search(r"\bquery:\s*string\b", code), (
        "TraceChrome no longer declares a `query: string` prop — is the filter still a prop?"
    )
    assert re.search(r"\bvalue=\{query\}", code), (
        "the filter input is no longer fed `value={query}` directly; if it now reads through a "
        "local variable, this guard can no longer tell a prop from a copy"
    )
    assert "useState" not in code, (
        "TraceChrome declares state — the filter box is a second copy of what the reader typed, "
        "and it blanks when an unrelated re-render (every pointermove of a strip drag) remounts it"
    )
