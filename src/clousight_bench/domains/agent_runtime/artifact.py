"""Ephemeral agent-artifact lifecycle: build -> upload -> (deploy) -> destroy.

The harness owns the whole artifact lifecycle so a real run needs only
credentials + a bucket, not a hand-uploaded zip: it zips the bundled benchmark
agent (shipped as package data, so this works from an installed wheel), uploads
it to OSS under a unique key, hands the reference to CreateAgentRuntime, and
deletes the object on teardown -- nothing is left behind.

The OSS SDK (``oss2``) is imported lazily; only a live upload/delete needs it.
The bucket client is injectable (``bucket_factory``) so the build/upload/delete
lifecycle and the provisioner wiring are fully testable without an account. The
exact ``oss2`` auth incantation is the one detail confirmed on the first live
run; everything around it is exercised offline.
"""
from __future__ import annotations

import importlib.resources as resources
import io
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

_BUNDLE = "clousight_bench.domains.agent_runtime.agent_bundle"
# Agent source files included at the zip root (flat, importable as siblings in FC)
_INCLUDE = ("agent.py", "lc_agent.py")
# protocol.py is the shared invoke/result contract — it lives in the open core so
# the probe client and the agent server can never drift. It is packed at the zip
# root too (agent.py does a top-level `import protocol` when running in FC/ECI).
_PROTOCOL_PKG = "clousight_bench.domains.agent_runtime"

# LangChain + OpenInference + OTel dependencies vendored into the zip.
# Installed via `pip install --target vendor/` at build time.
# Excluded from git (.gitignore) — regenerated on each build.
_LC_DEPS = [
    "langchain==0.3.*",          # AgentExecutor + create_tool_calling_agent
    "langchain-core==0.3.*",     # BaseChatModel, BaseTool, LCEL
    "openinference-instrumentation-langchain",
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-http",
]
# Patterns to strip from the vendor directory to keep the zip small
_VENDOR_EXCLUDE = ("__pycache__", ".dist-info", ".pyc", "tests/", "test/", "docs/",
                   ".deps-hash")


def _deps_fingerprint() -> str:
    """Stable fingerprint of the pinned vendor deps — the vendor-cache key."""
    import hashlib

    return hashlib.sha256("\n".join(_LC_DEPS).encode()).hexdigest()


def _build_vendor_dir(vendor_path: Path) -> None:
    """Install LangChain + OTel deps into vendor_path using pip/uv."""
    import shutil
    import subprocess

    if vendor_path.exists():
        shutil.rmtree(vendor_path)
    vendor_path.mkdir(parents=True)

    # Try uv first (faster), fall back to pip
    for cmd in (["uv", "pip", "install"], ["pip", "install"]):
        result = subprocess.run(
            [*cmd, "--target", str(vendor_path), *_LC_DEPS, "-q"],
            capture_output=True,
        )
        if result.returncode == 0:
            return
    raise RuntimeError(
        f"Failed to install agent dependencies. "
        f"Run manually: pip install --target {vendor_path} {' '.join(_LC_DEPS)}"
    )


def _should_exclude(path: Path, vendor_root: Path) -> bool:
    rel = str(path.relative_to(vendor_root))
    return any(excl in rel for excl in _VENDOR_EXCLUDE)


def build_agent_zip_bytes(with_langchain: bool = True) -> bytes:
    """Zip the bundled benchmark agent into a deployable code package (in memory).

    When ``with_langchain=True`` (default), installs LangChain + OpenInference +
    OTel into a temporary vendor directory and bundles it in the zip so the
    deployed FC function has access to a genuine agent framework.  The vendor
    directory is cached next to this module between builds to avoid repeated
    pip installs.

    Reads source files from package data so this works from an installed wheel.
    """

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Agent source files (flat at zip root)
        for name in _INCLUDE:
            try:
                source = resources.files(_BUNDLE).joinpath(name).read_text(encoding="utf-8")
                zf.writestr(name, source)
            except FileNotFoundError:
                pass  # optional files (lc_agent.py may not exist in older builds)

        # Shared protocol contract, sourced from the open core (packed at root).
        proto = resources.files(_PROTOCOL_PKG).joinpath("protocol.py").read_text(encoding="utf-8")
        zf.writestr("protocol.py", proto)

        # Vendor dependencies (only when requested)
        if with_langchain:
            # Cache vendor dir next to this file to avoid re-installing on every
            # build. Invalidate on any change to _LC_DEPS (not just an empty dir),
            # so bumping a dependency actually triggers a rebuild.
            here = Path(__file__).parent
            vendor_cache = here / "agent_bundle" / "_vendor_cache"
            stamp = vendor_cache / ".deps-hash"
            want = _deps_fingerprint()
            fresh = (
                vendor_cache.exists()
                and any(vendor_cache.iterdir())
                and stamp.exists()
                and stamp.read_text().strip() == want
            )
            if not fresh:
                _build_vendor_dir(vendor_cache)
                stamp.write_text(want)
            for f in vendor_cache.rglob("*"):
                if f.is_file() and not _should_exclude(f, vendor_cache):
                    arcname = f"vendor/{f.relative_to(vendor_cache)}"
                    zf.write(f, arcname=arcname)

    return buf.getvalue()


def build_agent_zip(dest: Path | str, with_langchain: bool = True) -> Path:
    """Write the deployable agent zip to ``dest`` (for manual upload / inspection)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(build_agent_zip_bytes(with_langchain=with_langchain))
    return dest


_OSS_PACKAGE = "oss2"

# The OSS credential bridge is defined once (with the probe's OSS client) and
# re-exported here so OssArtifactStore and `from ...artifact import
# _ChainCredentialsProvider` share the single implementation.
from clousight_bench.domains.agent_runtime.probe.oss_client import (  # noqa: E402
    _ChainCredentialsProvider,
)


class OssArtifactStore:
    """Uploads the agent zip to OSS under a unique key and deletes it on teardown.

    Object-level lifecycle only -- it never creates or deletes the bucket, so it
    is safe against a caller-owned bucket. The endpoint defaults to the region's
    public OSS endpoint."""

    def __init__(
        self,
        bucket: str,
        region: str,
        *,
        endpoint: str | None = None,
        key_prefix: str = "clousight-bench/",
        bucket_factory: Callable[[], Any] | None = None,
        run_id: str | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("an OSS bucket name is required to manage the agent artifact")
        self._bucket_name = bucket
        self._region = region
        self._endpoint = endpoint or f"https://oss-{region}.aliyuncs.com"
        self._key_prefix = key_prefix
        self._bucket_factory = bucket_factory
        self._run_id = run_id
        self._bucket: Any = None

    def _bucket_client(self) -> Any:
        if self._bucket is None:
            self._bucket = (self._bucket_factory or self._default_bucket)()
        return self._bucket

    def _default_bucket(self) -> Any:
        # Unify OSS auth on the SAME default credential chain as the AgentRun
        # client, so every access method works for uploads too and stays
        # consistent: long-term AccessKeys (env), STS temporary credentials
        # (security_token carried through), CLI profile, OIDC, and instance RAM
        # role -- not only static env-var AccessKeys.
        try:
            import oss2
            from alibabacloud_credentials.client import Client as CredClient
        except ImportError as exc:
            raise RuntimeError(
                f"the OSS SDK + credential chain are required to upload the agent "
                f"artifact but are not installed. Install them with: pip install "
                f"{_OSS_PACKAGE} alibabacloud-credentials (they ship with the "
                f"`aliyun` extra). Or upload the zip yourself and pass "
                f"target.artifact_ref."
            ) from exc
        auth = oss2.ProviderAuthV4(_ChainCredentialsProvider(CredClient()))
        return oss2.Bucket(auth, self._endpoint, self._bucket_name, region=self._region)

    def upload(self, data: bytes | None = None) -> str:
        """Put the agent zip under a unique key; return its ``oss://`` reference.

        When a run_id is known the key is namespaced under it
        (``<prefix><run_id>/<uuid>.zip``) so the object is attributable to the run
        for cost reconciliation and audit; otherwise a bare unique key is used."""
        payload = build_agent_zip_bytes() if data is None else data
        stem = f"{self._run_id}/{uuid.uuid4().hex}" if self._run_id else uuid.uuid4().hex
        key = f"{self._key_prefix}{stem}.zip"
        self._bucket_client().put_object(key, payload)
        return f"oss://{self._bucket_name}/{key}"

    def delete(self, ref: str) -> None:
        """Delete a previously uploaded object. Raises if the delete call fails so
        the caller can report a failed cleanup; a foreign ref is a no-op."""
        prefix = f"oss://{self._bucket_name}/"
        if not ref.startswith(prefix):
            return
        key = ref[len(prefix):]
        self._bucket_client().delete_object(key)
