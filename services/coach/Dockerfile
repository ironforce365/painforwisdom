FROM python:3.11-slim

# Build deps for sentence-transformers / faster-whisper
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libsndfile1 git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[test]" || pip install --no-cache-dir -e .

COPY . /app
ENV PYTHONPATH=/app
