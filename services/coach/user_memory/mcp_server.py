"""Per-user scratchpad memory, wrapping the memory_20250818 tool surface.

Each Telegram user gets `<base_dir>/<user_id>/` to store free-form notes the
agent uses to remember context across sessions. Backed by FastMCP stdio."""
from __future__ import annotations
import os
from pathlib import Path
from fastmcp import FastMCP

from shared.validation import validate_telegram_user_id


class UserMemory:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _user_root(self, user_id: str) -> Path:
        validate_telegram_user_id(user_id)
        d = self.base_dir / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _resolve(self, user_id: str, path: str) -> Path:
        root = self._user_root(user_id)
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as e:
            raise ValueError(f"path traversal rejected: {path!r}") from e
        return candidate

    def view(self, user_id: str, path: str) -> str:
        return self._resolve(user_id, path).read_text(encoding="utf-8")

    def create(self, user_id: str, path: str, content: str) -> None:
        p = self._resolve(user_id, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def list(self, user_id: str) -> list[str]:
        root = self._user_root(user_id)
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())

    def delete(self, user_id: str, path: str) -> None:
        self._resolve(user_id, path).unlink(missing_ok=True)


mcp = FastMCP("coach-user-memory")
_store: UserMemory | None = None


def _get_store() -> UserMemory:
    """Lazy singleton — defers `/data/user_memory` mkdir until first tool call.

    Keeps test collection from failing when COACH_USER_MEMORY_DIR is unset and
    the default `/data/user_memory` is not writable (e.g. dev machines)."""
    global _store
    if _store is None:
        _store = UserMemory(base_dir=Path(os.environ.get("COACH_USER_MEMORY_DIR", "/data/user_memory")))
    return _store


@mcp.tool()
def memory_view(user_id: str, path: str) -> str:
    """Read a per-user note. user_id must be the Telegram numeric ID string."""
    return _get_store().view(user_id, path)


@mcp.tool()
def memory_create(user_id: str, path: str, content: str) -> str:
    """Write a per-user note. Overwrites if exists. Path must be relative to
    the user's scratchpad root; absolute paths and `..` traversal are rejected."""
    _get_store().create(user_id, path, content)
    return "ok"


@mcp.tool()
def memory_list(user_id: str) -> list[str]:
    """List all note paths for a user, relative to their scratchpad root."""
    return _get_store().list(user_id)


@mcp.tool()
def memory_delete(user_id: str, path: str) -> str:
    """Delete a per-user note. No-op if the note does not exist."""
    _get_store().delete(user_id, path)
    return "ok"


if __name__ == "__main__":
    mcp.run(transport="stdio")
