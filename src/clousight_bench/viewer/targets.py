"""The target configs the console may read: ``configs/*.yaml``.

A target is an adapter's instance configuration — the endpoint, the region, the
names of the env vars holding credentials. It is the one thing in this tool the
console is allowed to *write*, which is why the whole module is written as if
every name it receives is hostile: names arrive over HTTP, and a name that
reaches the filesystem unchecked is a path.

Two rules carry the safety here, and both are needed:

1. **A name is a name.** ``^[A-Za-z0-9._-]{1,64}$``, with ``.`` and ``..``
   excluded — they match the pattern perfectly well and name a directory.
2. **The resolved path must still be inside.** Checked after ``resolve()``,
   because a symlink passes any name check ever written.

And one rule about what goes back out: a value whose key looks like a
credential is replaced by ``***``. Our configs are written to hold env var
*names* rather than secrets, so this should never fire — which is exactly why
it must exist, for the file where someone pasted the real thing.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The suffix a target file has. One spelling only: ``.yml`` would make two
#: names resolve to one target and turn "does it exist" into a question.
TARGET_SUFFIX = ".yaml"

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

#: A key whose *name* suggests its value is a credential. Matched as a
#: substring, case-folded: ``access_key_secret``, ``apiToken``, ``PASSWORD``.
_SECRET_RE = re.compile(r"secret|token|password|key|credential", re.IGNORECASE)

_REDACTED = "***"


def valid_target_name(name: str) -> bool:
    """Whether ``name`` is a plain token that could name a target file.

    Builds no path, touches no disk: a caller uses it to tell "malformed" from
    "well-formed but absent", which are a 400 and a 404 respectively.
    """
    return bool(_NAME_RE.match(name)) and name not in (".", "..")


def target_path(configs_dir: Path, name: str) -> Path | None:
    """Where target ``name`` lives, or None if it could not live there.

    Answers for a file that does not exist yet — a new target has to be
    writable somewhere — so "None" means *forbidden*, never *absent*.
    """
    if not valid_target_name(name):
        return None
    root = configs_dir.resolve()
    candidate = (configs_dir / f"{name}{TARGET_SUFFIX}").resolve()
    if candidate.parent != root:
        # Covers the symlink case a name check cannot: the link's own name is
        # spotless, and its target is /etc.
        logger.warning("viewer: target %r resolves outside the configs directory", name)
        return None
    return candidate


def redact(value: Any, _prefix: str = "") -> tuple[Any, list[str]]:
    """A copy of ``value`` with credential-shaped values replaced by ``***``.

    Returns the copy and the dotted paths that were hidden — the caller shows
    that list, because "this was redacted" and "this was empty" must not read
    the same on screen.
    """
    hidden: list[str] = []
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            path = f"{_prefix}{key}"
            if isinstance(key, str) and _SECRET_RE.search(key) and not isinstance(item, (dict, list)):
                out[key] = _REDACTED
                hidden.append(path)
            else:
                out[key], nested = redact(item, f"{path}.")
                hidden.extend(nested)
        return out, hidden
    if isinstance(value, list):
        items = []
        for index, item in enumerate(value):
            clean, nested = redact(item, f"{_prefix}{index}.")
            items.append(clean)
            hidden.extend(nested)
        return items, hidden
    return value, hidden


def _parse(path: Path) -> tuple[dict[str, Any], str]:
    """The file's mapping, or an empty one and why it is empty.

    A config that will not parse stays *visible* with its error attached: a
    broken file that vanishes from the list looks exactly like a deleted one.
    """
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"could not read: {exc.__class__.__name__}"
    except yaml.YAMLError as exc:
        return {}, f"not valid YAML: {str(exc).splitlines()[0][:160]}"
    if raw is None:
        return {}, ""
    if not isinstance(raw, dict):
        return {}, f"a target must be a mapping, not {type(raw).__name__}"
    return raw, ""


def _summary(name: str, path: Path, data: dict[str, Any], error: str) -> dict[str, Any]:
    target = data.get("target")
    target = target if isinstance(target, dict) else {}
    stat = path.stat()
    return {
        "name": name,
        "filename": path.name,
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "mode": str(target.get("mode") or ""),
        "provider": str(target.get("provider") or ""),
        "region": str(target.get("region") or ""),
        "error": error,
    }


def list_targets(configs_dir: Path) -> list[dict[str, Any]]:
    """Every target in ``configs_dir``, by name.

    A missing directory lists nothing rather than raising: a fresh checkout has
    no configs and that is a normal state, not a failure.
    """
    if not configs_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(configs_dir.glob(f"*{TARGET_SUFFIX}")):
        name = path.name[: -len(TARGET_SUFFIX)]
        if not valid_target_name(name) or target_path(configs_dir, name) is None:
            continue  # a file the console could never address anyway
        data, error = _parse(path)
        out.append(_summary(name, path, data, error))
    return out


def load_target(configs_dir: Path, name: str) -> dict[str, Any] | None:
    """One target: its summary, its redacted structure, and its text if safe.

    ``yaml`` is the file's own bytes and is served **only when nothing had to
    be redacted**. Comments and key order are most of what a config file is,
    so the console edits the real text where it can; where it cannot, it says
    so rather than shipping a secret or a lossy re-dump pretending to be the
    file.
    """
    path = target_path(configs_dir, name)
    if path is None or not path.is_file():
        return None
    data, error = _parse(path)
    clean, hidden = redact(data)
    entry = _summary(name, path, data, error)
    entry["data"] = clean
    entry["redacted"] = hidden
    entry["yaml"] = None if hidden else path.read_text(encoding="utf-8")
    return entry
