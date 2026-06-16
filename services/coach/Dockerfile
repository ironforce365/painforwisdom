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
# Dependency layer — keyed ONLY on requirements.txt (the dependency manifest),
# NOT on pyproject.toml. A release bumps the version in pyproject.toml but does
# NOT touch requirements.txt, so this heavy layer (torch pulls ~5GB of CUDA)
# stays cached on a version-only bump and is skipped entirely — no reinstall,
# no unpack. It rebuilds only on a real dependency change, i.e. when you
# regenerate requirements.txt from pyproject (test_requirements_sync.py fails if
# the two drift). Wheels still come from the BuildKit pip cache mount.
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . /app
# Install the local package itself (editable). Deps are already satisfied above,
# so --no-deps keeps this trivial: this is the ONLY layer a version-only bump
# rebuilds, and it does zero torch work.
RUN pip install -e . --no-deps
ENV PYTHONPATH=/app
