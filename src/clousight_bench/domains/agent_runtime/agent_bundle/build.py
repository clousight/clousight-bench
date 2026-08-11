"""Package the benchmark agent into agent.zip for CreateAgentRuntime
(codeConfiguration / OSS). Stdlib-only sources, so the zip has no deps.

The files are flattened to the zip root (agent.py imports protocol.py as a
sibling module there). The AgentRun code-package entrypoint contract (how the
runtime launches the server) is validated on a live account -- that bootstrap
line is the one account-gated part; the zip contents and the local server are
account-free.
"""
from __future__ import annotations

import zipfile
from importlib import resources
from pathlib import Path

_SOURCES = ("agent.py",)
# protocol.py is the shared invoke/result contract — it lives in the open core
# so probe client and agent server can never drift; packed at the zip root
# (agent.py imports it as a sibling module there).
_PROTOCOL_PKG = "clousight_bench.domains.agent_runtime"


def build_artifact(out_dir: Path) -> Path:
    here = Path(__file__).parent
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / "agent.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in _SOURCES:
            src = here / name
            if src.exists():
                zf.write(src, arcname=name)
        proto = resources.files(_PROTOCOL_PKG).joinpath("protocol.py").read_text(encoding="utf-8")
        zf.writestr("protocol.py", proto)
    return zpath
