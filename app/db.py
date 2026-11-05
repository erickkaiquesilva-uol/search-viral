"""Persistência simples em SQLite: histórico de buscas e cache de respostas da IA."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,            -- 'viral' | 'research'
    query TEXT NOT NULL,
    params TEXT NOT NULL,
    created_at TEXT NOT NULL,
    result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_cache (
    key TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def connect():
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_search(kind: str, query: str, params: dict, result: dict) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO searches (kind, query, params, created_at, result) VALUES (?, ?, ?, ?, ?)",
            (kind, query, json.dumps(params, ensure_ascii=False), _now(), json.dumps(result, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def get_search(search_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "query": row["query"],
        "params": json.loads(row["params"]),
        "created_at": row["created_at"],
        "result": json.loads(row["result"]),
    }


def list_searches(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, query, params, created_at FROM searches ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {"id": r["id"], "kind": r["kind"], "query": r["query"], "params": json.loads(r["params"]), "created_at": r["created_at"]}
        for r in rows
    ]


def delete_search(search_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))


def cache_get(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT response FROM ai_cache WHERE key = ?", (key,)).fetchone()
    return row["response"] if row else None


def cache_set(key: str, response: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO ai_cache (key, response, created_at) VALUES (?, ?, ?)", (key, response, _now())
        )
