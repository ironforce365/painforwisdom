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
RUN pip install --no-cache-dir -e ".[test]" || pip install --no-cache-dir -e .

COPY . /app
ENV PYTHONPATH=/app
