# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Build deps for sentence-transformers / faster-whisper
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libsndfile1 git curl ca-certificates && rm -rf /var/lib/apt/lists/*

# Claude Code CLI — the claude-agent-sdk spawns `claude` as a subprocess for
# every turn (auth via CLAUDE_CODE_OAUTH_TOKEN). Without it the agent can't run.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && claude --version \
    && rm -rf /var/lib/apt/lists/* /root/.npm

WORKDIR /app
COPY pyproject.toml ./
# Cache pip downloads on the host across builds (BuildKit cache mount). A
# version-only bump in pyproject.toml busts this layer, but the wheels — torch
# pulls ~5GB of CUDA — come from the cache instead of re-downloading (the ~40min
# rebuild). The mount lives outside the image, so dropping --no-cache-dir does
# NOT bloat the layer: pip's cache dir IS the mount.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e ".[test]" || pip install -e .

COPY . /app
ENV PYTHONPATH=/app
