# cb-probe: in-region ECI poller for the OSS-mediated private probe path.
#
# Build context: repo root  (docker build -f deploy/cb-probe/Dockerfile .)
# Runtime env:  CB_PROBE_BUCKET, CB_PROBE_REGION, CB_PROBE_CONTROL_PREFIX
#               (optional: CB_PROBE_TOKEN)
#
# The container installs only the probe extra (requests + oss2) — no LangChain,
# no OTel, no heavy agent bundle. The base package (pyyaml) and the probe
# subpackage are all that run here.

FROM python:3.12-slim

# Create a non-root user so the process doesn't run as root inside ECI.
RUN groupadd --system cbprobe && useradd --system --gid cbprobe --no-create-home cbprobe

WORKDIR /app

# Copy only files needed for `pip install .` — src/, pyproject.toml, README.md.
# (The .dockerignore at repo root keeps the build context small.)
COPY pyproject.toml README.md ./
COPY src/ src/

# Install the package with the probe extra: requests + oss2 + base (pyyaml).
# --no-cache-dir keeps the layer lean; no build tools needed for these wheels.
RUN pip install --no-cache-dir ".[probe]"

USER cbprobe

ENTRYPOINT ["python", "-m", "clousight_bench.domains.agent_runtime.probe.agent_loop"]
