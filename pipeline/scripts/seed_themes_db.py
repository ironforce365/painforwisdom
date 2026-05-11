"""Re-apply the canonical theme taxonomy to themes.db.

The DB auto-seeds itself on first connect (see `pipeline.themes_db.connect`),
so this script is only needed when:
- The seed list in `pipeline/themes_seed.py` was edited and the DB already
  exists; running this script upserts the changes.
- The operator wants to verify the DB matches the in-code source of truth.

Idempotent. Usage:

    python -m pipeline.scripts.seed_themes_db
"""
from __future__ import annotations

import sys

from pipeline.themes_db import connect, upsert_many
from pipeline.themes_seed import SEED


def main(_argv: list[str]) -> int:
    conn = connect(auto_seed=False)
    try:
        upsert_many(conn, SEED)
        active = conn.execute(
            "SELECT COUNT(*) FROM themes WHERE status='active'"
        ).fetchone()[0]
        dead = conn.execute(
            "SELECT COUNT(*) FROM themes WHERE status='dead'"
        ).fetchone()[0]
        print(f"✓ themes.db seeded: {active} active + {dead} dead = {active + dead} total")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
