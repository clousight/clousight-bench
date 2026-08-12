"""Zero-dependency plugin-API version-range checking.

The plugin surface negotiates on a dotted-numeric version. We support just the
comparison operators a range needs (``>= > <= < ==``) joined by comma-AND, which
is all ``requires_plugin_api`` declarations need. We deliberately do NOT depend
on ``packaging`` -- the core stays pyyaml-only.
"""

from __future__ import annotations


class VersioningError(ValueError):
    """A version string or range could not be parsed."""


def parse_version(s: str) -> tuple[int, ...]:
    s = s.strip()
    if not s:
        raise VersioningError("empty version string")
    parts = s.split(".")
    out: list[int] = []
    for p in parts:
        if not p.isdigit():
            raise VersioningError(f"non-numeric version component {p!r} in {s!r}")
        out.append(int(p))
    return tuple(out)


_OPS = ("<=", ">=", "==", "<", ">")  # longest-match first


def _compare(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    # pad to equal length so (1, 0) == (1,)
    n = max(len(a), len(b))
    a2 = a + (0,) * (n - len(a))
    b2 = b + (0,) * (n - len(b))
    return (a2 > b2) - (a2 < b2)


def _satisfies_clause(clause: str, version: tuple[int, ...]) -> bool:
    clause = clause.strip()
    for op in _OPS:
        if clause.startswith(op):
            bound = parse_version(clause[len(op) :])
            c = _compare(version, bound)
            return {
                "<=": c <= 0,
                ">=": c >= 0,
                "==": c == 0,
                "<": c < 0,
                ">": c > 0,
            }[op]
    raise VersioningError(f"unsupported version clause {clause!r}")


def range_contains(range_str: str, version_str: str) -> bool:
    version = parse_version(version_str)
    clauses = [c for c in range_str.split(",") if c.strip()]
    if not clauses:
        raise VersioningError(f"empty version range {range_str!r}")
    return all(_satisfies_clause(c, version) for c in clauses)
