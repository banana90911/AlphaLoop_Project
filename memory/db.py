"""
description:        PostgreSQL 단일 진입점 (모든 프로세스가 이 connect()만 거침)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.numeric import FloatLoader

from config.settings import get_settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
CONNECT_TIMEOUT_S = 5


def connect(dsn: str | None = None, *, read_only: bool = False) -> psycopg.Connection:
    """공유 DB 연결을 만든다. read_only=True는 대시보드 등 읽기 전용 프로세스용."""
    s = get_settings()
    fallback = (s.db_dsn_readonly or s.db_dsn) if read_only else s.db_dsn
    target = dsn or fallback
    conn = psycopg.connect(  # noqa: TID251
        target, row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT_S
    )
    conn.adapters.register_loader("numeric", FloatLoader)
    if read_only:
        # 전용 계정이 없는 환경(개발)에서도 쓰기를 막는 2차 방어. 계정 권한이 1차다.
        conn.read_only = True
    return conn


def init_db(dsn: str | None = None) -> psycopg.Connection:
    """schema.sql을 적용해 표를 생성/갱신하고(멱등) 연결을 반환한다."""
    conn = connect(dsn)
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn
