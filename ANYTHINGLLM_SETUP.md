# AnythingLLM Setup for PainForWisdom KB

## Status: Working ✅

### What was fixed
The Xeon E5-2697 v2 CPU (2013 era, no AVX2/AVX-512) crashed AnythingLLM's
bundled LanceDB native module on every chat attempt with an "invalid opcode"
kernel trap. Switched vector store to **Qdrant** which runs cleanly on older CPUs.

---

## Current Configuration

**File:** `~/.config/anythingllm-desktop/storage/.env`

```
LLM_PROVIDER='ollama'
OLLAMA_BASE_PATH='http://127.0.0.1:11434'
OLLAMA_MODEL_PREF='qwen2.5:14b'
OLLAMA_KEEP_ALIVE_TIMEOUT='300'
STORAGE_DIR='/home/gonzalo/.config/anythingllm-desktop/storage'
SERVER_PORT='3001'
APP_DISCOVERABLE='true'
COLLECTOR_PORT='8888'
VECTOR_DB='qdrant'
QDRANT_ENDPOINT='http://127.0.0.1:6333'
```

## Qdrant

- Binary: `~/.local/bin/qdrant` (v1.17.0)
- Storage: `~/.config/qdrant/storage`
- Systemd user service: enabled (auto-starts on login)
- Manual start: `systemctl --user start qdrant`

**Check it's running:**
```bash
curl http://127.0.0.1:6333
```

## Ollama Models

- `qwen2.5:14b` — LLM (chat)
- `nomic-embed-text` — embedder (document search)

---

## Remaining Setup Steps (one-time, in the UI)

### 1. Configure Embedder
Settings → Embedder → select **Ollama** → model `nomic-embed-text:latest` → Save

### 2. Create Workspace
New Workspace → name: **PainForWisdom Research**

Workspace settings:
- LLM: System Default (Ollama / qwen2.5:14b)
- Chat mode: **Query** (answers only from documents, not hallucinated)
- Similarity threshold: **0.75**

### 3. Import Documents
Upload these files to the workspace:
- All files from `obsidian-vault/gonzalo-book/entries/` (18 vault entries)
- `obsidian-vault/gonzalo-book/research-index.md`

### 4. System Prompt (paste into workspace System Prompt field)
```
You are a research assistant for Gonzalo, an ultra runner, engineer, and author
building a knowledge base around performance, resilience, and coaching philosophy.

Answer questions using ONLY the provided documents. When citing information,
reference the specific vault entry date and topic. Focus on:
- Scientific evidence and study findings
- Practical applications for ultra running and coaching
- Connections between research topics and real-world performance

If the documents don't contain enough information to answer the question, say so clearly.
```

---

## Troubleshooting

**If chat stops working after reboot:**
```bash
# Start Qdrant
systemctl --user start qdrant

# Verify
curl http://127.0.0.1:6333
```

**If Ollama isn't responding:**
```bash
sudo systemctl restart ollama
curl http://127.0.0.1:11434
```

**AnythingLLM backend crash (check journal):**
```bash
journalctl --no-pager -n 50 | grep -i "anythingllm\|invalid opcode\|backend"
```
