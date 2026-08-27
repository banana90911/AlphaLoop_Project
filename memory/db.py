"""PostgreSQL 단일 진입점 (03-arch 3.3 · 10-ops 10.2).

매매 코어·회고·대시보드는 *모두* 이 `connect()`만 거쳐 같은 DB에 닿는다.
직접 `psycopg.connect()` 호출은 금지(ruff TID251로 강제) — 이 모듈만 예외.

파일 DB가 아니라 서비스를 쓰는 이유는 프로세스가 둘이기 때문이다 — 매매 코어(쓰기)와
대시보드 API(읽기)가 같은 데이터를 다루고, 대시보드가 절대 쓰지 못한다는 보장을
DB 계정 권한으로 강제한다(07-model). `read_only=True`가 그 대시보드 경로다.

행은 dict으로 돌려준다(`row["SymbolId"]`). 컬럼명이 PascalCase인 것은 DDL이 큰따옴표로
감싼 결과다 — 감싸지 않으면 PostgreSQL이 전부 소문자로 접는다.
"""
from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.numeric import FloatLoader

from config.settings import get_settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
CONNECT_TIMEOUT_S = 5


def connect(dsn: str | None = None, *, read_only: bool = False) -> psycopg.Connection:
    """공유 DB 연결. read_only=True는 대시보드 등 읽기 전용 프로세스용(08-dashboard 8.1).

    금액 컬럼은 numeric이라 psycopg가 기본으로 Decimal을 돌려주는데, 파이썬 쪽 계산은
    전부 float이라 섞이면 산술이 터진다. 정밀도는 *저장*에서 지키고 읽을 때 float으로
    통일한다 — 반올림 오차 누적을 막자는 목적은 DB에 들어간 값이 정확하면 달성된다.
    """
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
    """schema.sql을 적용해 표를 생성/갱신하고 연결을 반환한다(멱등 — IF NOT EXISTS).

    DB(데이터베이스) 자체는 미리 있어야 한다 — 파일 DB와 달리 접속만으로 생기지 않는다.
    """
    conn = connect(dsn)
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn
