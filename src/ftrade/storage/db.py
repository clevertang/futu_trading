"""SQLite 连接与建表。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class Database:
    """薄封装：建表 + upsert + DataFrame 查询。"""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.conn = connect(self.path)
        self.migrate()

    def migrate(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---------- 写入 ----------

    def upsert(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        rows = [r for r in rows if r]
        if not rows:
            return 0
        cols = list(rows[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        payload = [tuple(r.get(c) for c in cols) for r in rows]
        with self.tx() as conn:
            conn.executemany(sql, payload)
        return len(payload)

    def set_state(self, key: str, value: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sync_state (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                (key, value),
            )

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    # ---------- 读取 ----------

    def query(self, sql: str, params: Sequence[Any] = ()) -> pd.DataFrame:
        return pd.read_sql_query(sql, self.conn, params=tuple(params))

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return row[0] if row else None
