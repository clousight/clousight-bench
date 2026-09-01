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
