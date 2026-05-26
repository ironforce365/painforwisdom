"""Nightly full rebuild of the vault RAG index."""
from __future__ import annotations
import logging
import os
import sys
from pathlib import Path

from vault_rag.builder import build_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vault_rag.rebuild")


def main() -> int:
    vault = Path(os.environ["COACH_VAULT_PATH"])
    storage = Path(os.environ["COACH_INDEX_STORAGE_DIR"])
    storage.mkdir(parents=True, exist_ok=True)
    log.info("rebuilding vault index from %s into %s", vault, storage)
    build_index(vault, storage)
    log.info("rebuild complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
