"""Benchmark asset resolution: bundled / public-remote / private, one mechanism.

A benchmark is more than code. Its *assets* (datasets, corpora, held-out scoring
keys) split into three tiers, and the framework resolves all three the same way
so the open/private boundary is a config choice, not a code fork:

- **bundled**  — small, license-clean, shipped in the repo (relative to the
  workload/task dir). Verified by sha256 if declared.
- **remote**   — large PUBLIC datasets (TPC-DS, SWE-bench, ...). NOT vendored:
  declared by uri + sha256 + license, downloaded on demand, checksum-verified,
  and cached. Keeps the OSS core light and license-auditable.
- **private**  — proprietary datasets / contamination-resistant scoring keys.
  Resolved by a registered private resolver (entry point
  ``clousight_bench.asset_resolvers``, shipped only in commercial packs). With
  no resolver installed, resolution raises ``NeedLicense`` -- never a crash, a
  clear "this asset is a licensed asset" message.

An asset's identity (``name@version`` + ``sha256``) is safe to fold into a
result's config_hash for reproducibility; the asset *contents* (e.g. scoring
keys) never are.
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request

BUNDLED = "bundled"
REMOTE = "remote"
PRIVATE = "private"
_SOURCES = (BUNDLED, REMOTE, PRIVATE)

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "clousight-bench" / "assets"


class AssetError(RuntimeError):
    pass


class NeedLicense(AssetError):
    """A private asset was requested but no licensed resolver is installed."""


@dataclass
class AssetSpec:
    name: str
    source: str  # bundled | remote | private
    uri: str = ""  # bundled: relative path; remote: URL; private: opaque ref
    sha256: str = ""  # hex digest (no "sha256:" prefix); optional for bundled
    license: str = ""  # required for remote (auditability)
    version: str = "0"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssetSpec:
        missing = {"name", "source"} - set(data)
        if missing:
            raise AssetError(f"asset missing required key(s): {sorted(missing)}")
        source = str(data["source"])
        if source not in _SOURCES:
            raise AssetError(f"asset {data['name']!r}: source must be one of {_SOURCES}, got {source!r}")
        spec = cls(
            name=str(data["name"]),
            source=source,
            uri=str(data.get("uri", "")),
            sha256=str(data.get("sha256", "")).removeprefix("sha256:"),
            license=str(data.get("license", "")),
            version=str(data.get("version", "0")),
        )
        if source == REMOTE and not spec.uri:
            raise AssetError(f"asset {spec.name!r}: remote source needs a uri")
        if source == REMOTE and not spec.license:
            raise AssetError(f"asset {spec.name!r}: remote source needs a license (auditability)")
        return spec

    def identity(self) -> dict[str, str]:
        """Reproducibility-safe identity (never the contents)."""
        return {"name": self.name, "version": self.version,
                "source": self.source, "sha256": self.sha256}


def load_asset_specs(manifest: dict[str, Any]) -> list[AssetSpec]:
    return [AssetSpec.from_dict(a) for a in (manifest.get("assets") or [])]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(path: Path, expected: str, name: str) -> None:
    if not expected:
        return
    actual = _sha256_file(path)
    if actual != expected:
        raise AssetError(
            f"asset {name!r} sha256 mismatch: expected {expected}, got {actual} ({path})"
        )


def resolve_asset(
    spec: AssetSpec,
    *,
    base_dir: Path | None = None,
    cache_dir: Path | None = None,
    private_resolver: Any | None = None,
) -> Path:
    """Return a local path to the asset's contents, fetching/verifying as needed.

    - bundled: resolve ``uri`` relative to ``base_dir`` (the workload/task dir).
    - remote:  download to cache (idempotent on a checksum hit), verify sha256.
    - private: delegate to ``private_resolver`` or an installed resolver; if none,
      raise ``NeedLicense``.
    """
    if spec.source == BUNDLED:
        if base_dir is None:
            raise AssetError(f"asset {spec.name!r}: bundled source needs base_dir")
        path = (Path(base_dir) / spec.uri).resolve()
        if not path.exists():
            raise AssetError(f"asset {spec.name!r}: bundled path not found: {path}")
        _verify(path, spec.sha256, spec.name)
        return path

    if spec.source == REMOTE:
        cache = Path(cache_dir or DEFAULT_CACHE_DIR)
        cache.mkdir(parents=True, exist_ok=True)
        suffix = Path(spec.uri).suffix
        dest = cache / f"{spec.name}-{spec.version}{suffix}"
        if dest.exists() and spec.sha256 and _sha256_file(dest) == spec.sha256:
            return dest  # checksum cache hit -> no re-download
        tmp = dest.with_suffix(dest.suffix + ".part")
        with request.urlopen(spec.uri, timeout=60) as resp, tmp.open("wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
        _verify(tmp, spec.sha256, spec.name)
        tmp.replace(dest)
        return dest

    # PRIVATE
    resolver = private_resolver or _installed_private_resolver()
    if resolver is None:
        raise NeedLicense(
            f"asset {spec.name!r} is a licensed private asset. Install a commercial "
            f"pack that registers a 'clousight_bench.asset_resolvers' entry point, "
            f"or provide credentials to the data service."
        )
    return Path(resolver.resolve(spec, cache_dir=cache_dir))


def _installed_private_resolver() -> Any | None:
    from clousight_bench.core.registry import load_asset_resolvers

    resolvers = load_asset_resolvers()
    return resolvers[0] if resolvers else None
