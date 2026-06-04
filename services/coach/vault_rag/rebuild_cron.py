"""Nightly full rebuild of the vault RAG index."""
from __future__ import annotations
import logging
import os
import shutil
import sys
from pathlib import Path

from vault_rag.builder import build_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vault_rag.rebuild")


def main() -> int:
    vault = Path(os.environ["COACH_VAULT_PATH"])
    storage = Path(os.environ["COACH_INDEX_STORAGE_DIR"])
    storage.parent.mkdir(parents=True, exist_ok=True)
    tmp = storage.with_name(storage.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    log.info("rebuilding vault index from %s into %s (via %s)", vault, storage, tmp)
    build_index(vault, tmp)
    if storage.exists():
        old = storage.with_name(storage.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        storage.rename(old)
    tmp.rename(storage)
    log.info("rebuild complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
