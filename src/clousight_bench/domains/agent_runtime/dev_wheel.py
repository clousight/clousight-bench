"""Dev-wheel fallback for the ECS probe carrier.

When the running control-plane version of ``clousight-bench`` has NOT been
published to the Aliyun PyPI mirror yet (any unreleased dev build), the ECS
carrier can't ``pip install 'clousight-bench[probe]==<ver>'`` — the version
simply isn't on the mirror. This module bridges that gap: it builds a wheel of
the *current source tree*, uploads it to OSS, and returns a presigned URL the
carrier uses as its ``code_spec``.

A presigned wheel URL can't carry a ``[probe]`` extra, so the probe extra's own
deps (``requests``/``oss2``) are surfaced separately via :func:`probe_extra_deps`
and installed from the mirror by the cloud-init script before the wheel.

Everything is injectable (OSS clients, the builder) so the wiring is unit-tested
account-free — no wheel is actually built and no bucket is touched in tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from clousight_bench.core.blobstore import BlobStore
from clousight_bench.domains.agent_runtime.carrier_base import CarrierError


class _Signer(Protocol):
    """An OSS client that can presign a URL (e.g. Oss2Client)."""

    def sign_url(self, key: str, expires: int = ..., method: str = ...) -> str: ...


def _repo_root() -> Path:
    """Locate the source tree root (the dir holding pyproject.toml).

    Only meaningful in a dev checkout — an installed wheel has no pyproject.toml
    above the package, which is exactly the case where the released package name
    should be used instead of this dev-wheel path.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise CarrierError(
        "cannot locate the source tree (no pyproject.toml above the package): "
        "dev-wheel fallback only works from a source checkout — install/publish "
        "clousight-bench and use the package-name code_spec instead."
    )


def build_probe_wheel_bytes(source_dir: str | Path | None = None) -> tuple[str, bytes]:
    """Build a wheel of the current clousight-bench source; return (filename, bytes).

    Prefers ``python -m build`` (PEP 517); falls back to ``pip wheel --no-deps``
    if the ``build`` package isn't installed. Raises CarrierError on failure so
    the carrier never silently ships a stale/absent artifact.
    """
    import subprocess
    import sys
    import tempfile

    root = Path(source_dir) if source_dir else _repo_root()
    with tempfile.TemporaryDirectory() as td:
        outdir = Path(td)
        try:
            subprocess.run(
                [sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir), str(root)],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            try:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "wheel",
                        "--no-deps",
                        "--wheel-dir",
                        str(outdir),
                        str(root),
                    ],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as exc:  # pragma: no cover - build env specific
                stderr = (exc.stderr or b"").decode(errors="replace")[-2000:]
                raise CarrierError(f"dev wheel build failed: {stderr}") from exc
        wheels = sorted(outdir.glob("clousight_bench-*.whl"))
        if len(wheels) != 1:
            raise CarrierError(
                f"dev wheel build produced {len(wheels)} clousight_bench-*.whl (expected exactly 1)"
            )
        wheel = wheels[0]
        return wheel.name, wheel.read_bytes()


def probe_extra_deps() -> list[str]:
    """Requirement specs of the ``[probe]`` extra, read from installed metadata.

    Falls back to the known deps if metadata is unavailable (e.g. running from a
    source tree that isn't ``pip install``-ed). Kept in sync with pyproject's
    ``[project.optional-dependencies].probe`` by reading it at runtime.
    """
    reqs: list[str] = []
    try:
        from importlib.metadata import metadata

        reqs = list(metadata("clousight-bench").get_all("Requires-Dist") or [])
    except Exception:  # noqa: BLE001 - metadata absent in a bare source checkout
        reqs = []
    out: list[str] = []
    for r in reqs:
        # e.g.  requests>=2.28; extra == "probe"
        if 'extra == "probe"' in r or "extra == 'probe'" in r:
            out.append(r.split(";", 1)[0].strip())
    return out or ["requests>=2.28", "oss2>=2.18"]


def deps_for_extras(extras: list[str]) -> list[str]:
    """Requirement specs for the given extras, read from installed metadata.

    A presigned wheel URL can't carry ``[extras]``, so the controller must
    pip-install each extra's deps from the mirror before the wheel. The prod
    controller needs probe (oss2/requests) + aliyun (alibabacloud SDKs, so the
    orchestrator can drive real runs) + store (duckdb/pyarrow for parquet).
    """
    try:
        from importlib.metadata import metadata

        reqs = list(metadata("clousight-bench").get_all("Requires-Dist") or [])
    except Exception:  # noqa: BLE001 - metadata absent in a bare source checkout
        reqs = []
    out: list[str] = []
    for r in reqs:
        for e in extras:
            if f'extra == "{e}"' in r or f"extra == '{e}'" in r:
                out.append(r.split(";", 1)[0].strip())
                break
    return out


def upload_dev_wheel(
    upload_client: BlobStore,
    sign_client: _Signer,
    campaign_id: str,
    *,
    wheel: tuple[str, bytes] | None = None,
    expires: int = 3600,
) -> str:
    """Upload the dev wheel and return a presigned GET URL for the carrier.

    *upload_client* PUTs the bytes (public OSS endpoint reachable from the
    control plane); *sign_client* presigns the URL (an ``internal=True`` client
    so the host is the VPC-internal endpoint the in-region ECS instance fetches).
    Signing is a local computation, so the internal endpoint need not be
    reachable from where this runs.

    NOTE on *expires*: this assumes the control plane authenticates with a STATIC
    RAM-user AK (the benchmark user's keys), so the presigned URL is valid for the
    full window. If the credential chain instead yields a TEMPORARY credential
    (STS / instance role), a V4 presigned URL is additionally bounded by that
    token's own expiry — which can be shorter than *expires* and cause pip's GET
    to fail mid-boot. Run the control plane with static AK for dev bring-ups.
    """
    name, data = wheel if wheel is not None else build_probe_wheel_bytes()
    key = f"clousight-bench/dev-wheels/{campaign_id or 'adhoc'}/{name}"
    upload_client.put_object(key, data)
    return sign_client.sign_url(key, expires=expires)
