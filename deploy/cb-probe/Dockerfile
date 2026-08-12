# cb-probe: in-region ECI poller for the OSS-mediated private probe path.
#
# Build context: repo root  (docker build -f deploy/cb-probe/Dockerfile .)
# Runtime env:  CB_PROBE_BUCKET, CB_PROBE_REGION, CB_PROBE_CONTROL_PREFIX
#               (optional: CB_PROBE_TOKEN)
#
# Everything is baked in at BUILD time (base + deps + probe code) so the running
# ECI container fetches NOTHING from the public internet — docker hub, github and
# pypi are all throttled from Aliyun cn-hangzhou. Sources default to Aliyun
# mirrors so this builds cleanly from inside China (e.g. Aliyun Cloud Shell).
# See deploy/cb-probe/README.md — no CI and no local Docker are required.

# Base image is pulled only at build time; override for a fully in-region build.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# PyPI index — Aliyun mirror by default (fast + reachable in-region). Override
# with --build-arg PIP_INDEX_URL=... when building outside China.
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# Create a non-root user so the process doesn't run as root inside ECI.
RUN groupadd --system cbprobe && useradd --system --gid cbprobe --no-create-home cbprobe

WORKDIR /app

# Copy only files needed for `pip install .` — src/, pyproject.toml, README.md.
# (The .dockerignore at repo root keeps the build context small.)
COPY pyproject.toml README.md ./
COPY src/ src/

# Bake the package + the probe extra (requests + oss2) via the Aliyun mirror.
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" ".[probe]"

USER cbprobe

ENTRYPOINT ["python", "-m", "clousight_bench.domains.agent_runtime.probe.agent_loop"]
