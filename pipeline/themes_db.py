"""Themes registry — sqlite cache over the canonical YAML source.

`data/themes.yaml` is the tracked source of truth. The sqlite DB at
`pipeline/state/themes.db` is a derived index used by:
- `pipeline.nodes.notion_research._resolve_agent` (theme → agent routing)
- `pipeline.scripts.render_curator_taxonomy` (regenerates the
  `.claude/agents/research-curator.md` taxonomy block)

`pipeline/state/` is gitignored, so the DB rebuilds itself from the YAML
on first `connect()` (auto-seed). Edit YAML, then either re-`connect()`
to a fresh DB or run `python -m pipeline.scripts.seed_themes_db` against
an existing DB to upsert.

Schema (single table; rows describe both umbrellas and sub-themes):

    themes(
      name          TEXT PRIMARY KEY,   -- kebab-case canonical name
      parent        TEXT NULL,          -- FK to themes(name); NULL for top-level
      status        TEXT NOT NULL,      -- 'active' | 'dead' (split umbrella)
      agent         TEXT NOT NULL,      -- agent name used by notion_research
      definition    TEXT NOT NULL,      -- one-line description for prompt
      priority      INTEGER NOT NULL,   -- priority ordering in the curator's
                                        -- "if X → this theme" rules
      priority_rule TEXT NOT NULL,      -- the rule text
      created_at    TEXT NOT NULL
    )

Env overrides:
- `PAINFORWISDOM_THEMES_DB`  — override DB path (default: pipeline/state/themes.db)
- `PAINFORWISDOM_THEMES_YAML` — override YAML path (default: data/themes.yaml)
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "pipeline" / "state" / "themes.db"
DEFAULT_YAML_PATH = PROJECT_ROOT / "data" / "themes.yaml"


def _db_path() -> Path:
    override = os.environ.get("PAINFORWISDOM_THEMES_DB", "").strip()
    return Path(override) if override else DEFAULT_DB_PATH


def _yaml_path() -> Path:
    override = os.environ.get("PAINFORWISDOM_THEMES_YAML", "").strip()
    return Path(override) if override else DEFAULT_YAML_PATH


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS themes (
    name          TEXT PRIMARY KEY,
    parent        TEXT NULL REFERENCES themes(name),
    status        TEXT NOT NULL CHECK (status IN ('active', 'dead')),
    agent         TEXT NOT NULL,
    definition    TEXT NOT NULL,
    priority      INTEGER NOT NULL,
    priority_rule TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_themes_parent ON themes(parent);
CREATE INDEX IF NOT EXISTS idx_themes_status ON themes(status);
"""


@dataclass(frozen=True)
class Theme:
    name: str
    parent: Optional[str]
    status: str
    agent: str
    definition: str
    priority: int
    priority_rule: str

    @property
    def is_umbrella(self) -> bool:
        return self.parent is None and self.status == "dead"

    @property
    def is_subtheme(self) -> bool:
        return self.parent is not None


def connect(path: Optional[Path] = None, *, auto_seed: bool = True) -> sqlite3.Connection:
    """Open (and create if missing) the themes DB. Caller closes.

    `pipeline/state/` is gitignored, so the DB file is regenerated per host
    on first connect. When `auto_seed` is True (default), an empty table is
    populated from `data/themes.yaml` (the tracked source of truth) so a
    fresh checkout works without a manual setup step.
    """
    p = path or _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    if auto_seed:
        _maybe_auto_seed(conn)
    return conn


def _maybe_auto_seed(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM themes").fetchone()[0]
    if count > 0:
        return
    upsert_many(conn, load_yaml_seed())


def load_yaml_seed(path: Optional[Path] = None) -> list[Theme]:
    """Read the canonical theme list from data/themes.yaml.

    Raises if the file is missing or malformed — callers expect the YAML
    to be tracked in git and present in every checkout.
    """
    p = path or _yaml_path()
    if not p.exists():
        raise FileNotFoundError(
            f"themes YAML not found at {p}. Set PAINFORWISDOM_THEMES_YAML or "
            f"restore data/themes.yaml from git."
        )
    raw = yaml.safe_load(p.read_text()) or {}
    rows = raw.get("themes")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"themes YAML at {p} has no `themes:` list")
    out: list[Theme] = []
    required = {"name", "status", "agent", "priority", "definition", "priority_rule"}
    for i, row in enumerate(rows):
        missing = required - row.keys()
        if missing:
            raise ValueError(f"themes YAML row {i}: missing fields {sorted(missing)}")
        out.append(
            Theme(
                name=row["name"],
                parent=row.get("parent"),
                status=row["status"],
                agent=row["agent"],
                definition=row["definition"],
                priority=int(row["priority"]),
                priority_rule=row["priority_rule"],
            )
        )
    return out


def upsert(conn: sqlite3.Connection, theme: Theme) -> None:
    conn.execute(
        """
        INSERT INTO themes (name, parent, status, agent, definition, priority, priority_rule, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            parent        = excluded.parent,
            status        = excluded.status,
            agent         = excluded.agent,
            definition    = excluded.definition,
            priority      = excluded.priority,
            priority_rule = excluded.priority_rule
        """,
        (
            theme.name,
            theme.parent,
            theme.status,
            theme.agent,
            theme.definition,
            theme.priority,
            theme.priority_rule,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def upsert_many(conn: sqlite3.Connection, themes: Iterable[Theme]) -> None:
    for t in themes:
        upsert(conn, t)
    conn.commit()


def _row_to_theme(row: sqlite3.Row) -> Theme:
    return Theme(
        name=row["name"],
        parent=row["parent"],
        status=row["status"],
        agent=row["agent"],
        definition=row["definition"],
        priority=row["priority"],
        priority_rule=row["priority_rule"],
    )


def get(conn: sqlite3.Connection, name: str) -> Optional[Theme]:
    row = conn.execute("SELECT * FROM themes WHERE name = ?", (name,)).fetchone()
    return _row_to_theme(row) if row else None


def get_agent(conn: sqlite3.Connection, name: str) -> Optional[str]:
    """Return the agent for a theme. Sub-themes route to their parent's agent."""
    t = get(conn, name)
    if t is None:
        return None
    if t.parent:
        parent = get(conn, t.parent)
        if parent:
            return parent.agent
    return t.agent


def list_active(conn: sqlite3.Connection) -> list[Theme]:
    rows = conn.execute(
        "SELECT * FROM themes WHERE status = 'active' ORDER BY priority ASC, name ASC"
    ).fetchall()
    return [_row_to_theme(r) for r in rows]


def list_dead(conn: sqlite3.Connection) -> list[Theme]:
    rows = conn.execute(
        "SELECT * FROM themes WHERE status = 'dead' ORDER BY name ASC"
    ).fetchall()
    return [_row_to_theme(r) for r in rows]


def list_children(conn: sqlite3.Connection, parent: str) -> list[Theme]:
    rows = conn.execute(
        "SELECT * FROM themes WHERE parent = ? ORDER BY priority ASC, name ASC",
        (parent,),
    ).fetchall()
    return [_row_to_theme(r) for r in rows]


def list_terminal_active(conn: sqlite3.Connection) -> list[Theme]:
    """Themes that the curator should actually pick: active and not split."""
    return [t for t in list_active(conn) if t.status == "active"]
