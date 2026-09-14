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
