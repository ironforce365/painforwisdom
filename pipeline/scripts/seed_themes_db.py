"""Re-apply data/themes.yaml to themes.db.

The DB auto-seeds itself from the YAML on first connect (see
`pipeline.themes_db.connect`), so this script is only needed when:
- `data/themes.yaml` was edited *and* the DB already exists on this host —
  the upsert applies the diff.
- The operator wants to verify the DB matches the YAML.

Idempotent. Usage:

    python -m pipeline.scripts.seed_themes_db
"""
from __future__ import annotations

import sys

from pipeline.themes_db import connect, load_yaml_seed, upsert_many


def main(_argv: list[str]) -> int:
    seed = load_yaml_seed()
    conn = connect(auto_seed=False)
    try:
        upsert_many(conn, seed)
        active = conn.execute(
            "SELECT COUNT(*) FROM themes WHERE status='active'"
        ).fetchone()[0]
        dead = conn.execute(
            "SELECT COUNT(*) FROM themes WHERE status='dead'"
        ).fetchone()[0]
        print(f"✓ themes.db seeded from data/themes.yaml: {active} active + {dead} dead = {active + dead} total")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
