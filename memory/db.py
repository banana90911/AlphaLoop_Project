"""SQLite 단일 진입점 (03-arch 3.3 · 11-2.2).

매매 코어·회고·대시보드는 *모두* 이 `connect()`만 거쳐 같은 파일에 닿는다.
직접 `sqlite3.connect()` 호출은 금지(ruff TID251로 강제) — 이 모듈만 예외.

PRAGMA: WAL(동시 읽기) · busy_timeout(잠금 대기) · foreign_keys(무결성).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from config.settings import get_settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
BUSY_TIMEOUT_MS = 5000


def connect(db_path: str | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    """공유 SQLite 연결. read_only=True는 대시보드 등 읽기 전용 프로세스용(8장 불변식)."""
    path = db_path or get_settings().db_path
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)  # noqa: TID251
    else:
        conn = sqlite3.connect(path)  # noqa: TID251
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | None = None) -> sqlite3.Connection:
    """schema.sql을 적용해 DB를 생성/갱신하고 연결을 반환한다(멱등 — IF NOT EXISTS)."""
    conn = connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn
