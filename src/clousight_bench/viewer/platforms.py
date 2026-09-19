"""The cloud platforms this build can measure, read from the plugin registry.

This is the catalogue answer to "what can we point a benchmark at". Every fact
here comes from the installed adapters themselves — their docstring, their
``status``, their ``DOCS`` link — rather than from a list maintained beside
them, because a hand-maintained list is the one that goes stale the week an
adapter lands.

**``status`` is the load-bearing field and it is not decoration.**
``reference`` is an offline stand-in, ``experimental`` reaches a real service,
and ``skeleton`` is wired but not yet proven against the vendor. A reader who
cannot tell those apart will read a skeleton's absence of results as "nobody
ran it yet" instead of "this cannot run yet", so the UI prints it on every row.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Adapter attributes we surface. Anything else stays internal: this endpoint
#: is a catalogue, not a dump of the plugin's shape.
_STATUS_UNKNOWN = "unknown"


def _summary(doc: str | None) -> str:
    """The adapter's docstring, collapsed to its first sentence-ish line.

    Adapters document themselves in prose that starts with what the thing IS
    ("Aliyun AgentRun (GA). Sessions map to..."), which is exactly the line a
    catalogue wants. Later paragraphs are implementation notes for whoever
    edits the adapter and would only bury the name.

    Those docstrings are RST, where ``like this`` marks an inline literal. The
    page renders text, not RST, so the backticks would arrive as backticks —
    which is what ycsb-local and benchbase-local shipped on screen. Dropping
    them keeps the word and loses the markup.
    """
    if not doc:
        return ""
    return " ".join(doc.replace("`", "").split()).split(". ")[0].rstrip(".")


def _docs_url(raw: object) -> str:
    """The adapter's ``DOCS``, if it is a plain URL.

    Several adapters annotate the link ("https://... (AgentArts)"), so only the
    leading token is taken. A value that is not a string, or not http(s), is
    dropped rather than rendered — a broken link in a catalogue is worse than
    no link.
    """
    if not isinstance(raw, str):
        return ""
    head = raw.strip().split()[0] if raw.strip() else ""
    return head if head.startswith(("http://", "https://")) else ""


def list_platforms(usage: dict[str, int] | None = None) -> dict[str, Any]:
    """Every domain and the platforms it can measure.

    ``usage`` maps ``"<domain>/<platform>"`` to how many sealed records used it;
    absent means the caller could not count, which is rendered as "—" rather
    than as 0. Those are different claims and the UI must not merge them.
    """
    # Imported lazily: loading the registry walks entry points, which is slow
    # enough to matter on a viewer that may never open this page.
    from clousight_bench.core.registry import load_domains

    counted = usage is not None
    usage = usage or {}
    domains: list[dict[str, Any]] = []
    try:
        packs = load_domains()
    except Exception as exc:  # noqa: BLE001 - a bad plugin must not 500 the page
        logger.warning("viewer: could not load domains for the platform catalogue: %s", exc)
        return {"domains": [], "counted": counted, "error": str(exc)[:200]}

    for domain_name, pack in sorted(packs.items()):
        platforms: list[dict[str, Any]] = []
        try:
            adapters = pack.adapters()
        except Exception as exc:  # noqa: BLE001
            logger.warning("viewer: domain %s exposed no adapters: %s", domain_name, exc)
            adapters = {}
        for platform_name, cls in sorted(adapters.items()):
            key = f"{domain_name}/{platform_name}"
            platforms.append(
                {
                    "platform": platform_name,
                    "summary": _summary(getattr(cls, "__doc__", "")),
                    "status": str(getattr(cls, "status", _STATUS_UNKNOWN)),
                    "docs": _docs_url(getattr(cls, "DOCS", "")),
                    "runs": usage.get(key, 0) if counted else None,
                }
            )
        domains.append(
            {
                "domain": domain_name,
                "description": str(getattr(pack, "description", "")),
                "platforms": platforms,
            }
        )
    return {"domains": domains, "counted": counted}


def platform_usage(records: list[dict[str, Any]]) -> dict[str, int]:
    """How many records each ``<domain>/<platform>`` produced.

    Counted from the sealed records rather than from a column anyone maintains,
    so it cannot disagree with what is actually on disk.
    """
    out: dict[str, int] = {}
    for record in records:
        domain = record.get("domain")
        platform = record.get("adapter")
        if isinstance(domain, str) and isinstance(platform, str) and domain and platform:
            out[f"{domain}/{platform}"] = out.get(f"{domain}/{platform}", 0) + 1
    return out
