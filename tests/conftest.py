"""DB 테스트 공통 픽스처 — 테스트마다 임시 스키마 하나(07-model).

파일 DB와 달리 PostgreSQL은 서버가 있어야 한다. 접속 대상을 이 순서로 찾는다:

1. `ALPHALOOP_TEST_DSN` 환경변수 — 실제 서버(CI·운영 환경 검증)
2. `pgserver` 패키지 — PostgreSQL 바이너리를 번들한 pip 패키지. 임시 디렉터리에
   1인용 서버를 띄운다. 맥에 PostgreSQL을 설치하지 않고도 DB 테스트가 돈다
3. 설정의 `db_dsn` — 개발자가 띄워둔 로컬 서버

셋 다 안 되면 DB 테스트만 조용히 skip한다 — 순수 계산 테스트(점수·비용·청산 규칙)는
서버 없이도 그대로 돈다.

격리는 스키마 단위로 한다. 테스트마다 `test_<난수>` 스키마를 만들고 search_path를
거기로 돌린 뒤 schema.sql을 적용하고, 끝나면 통째로 DROP한다 — 표를 하나씩 지우는
것보다 빠르고, 표가 늘어도 픽스처를 고칠 일이 없다.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from config.settings import get_settings
from memory.db import SCHEMA_PATH, connect

# pgserver가 띄운 서버 핸들. 놓으면 서버가 내려가므로 세션 내내 붙잡아 둔다.
_EMBEDDED = None


def _embedded_dsn() -> str | None:
    """pgserver로 1인용 서버를 띄우고 DSN을 반환. 패키지가 없으면 None."""
    global _EMBEDDED
    try:
        import pgserver
    except ImportError:
        return None
    if _EMBEDDED is None:
        # 데이터 디렉터리는 재사용한다 — 매번 initdb를 돌리면 세션당 수 초가 날아간다.
        data_dir = Path(tempfile.gettempdir()) / "alphaloop-pgtest"
        data_dir.mkdir(parents=True, exist_ok=True)
        _EMBEDDED = pgserver.get_server(data_dir)
    return _EMBEDDED.get_uri()


@pytest.fixture(scope="session")
def dsn() -> str:
    return os.environ.get("ALPHALOOP_TEST_DSN") or _embedded_dsn() or get_settings().db_dsn


@pytest.fixture
def conn(dsn):
    """빈 스키마가 적용된 매매 코어용 연결. 서버가 없으면 이 테스트를 skip."""
    try:
        c = connect(dsn)
    except psycopg.Error as e:                       # 서버 없음·DB 없음·권한 없음
        pytest.skip(f"PostgreSQL 접속 불가({dsn}): {e}")
    schema = f"test_{uuid4().hex[:12]}"
    try:
        c.execute(f'CREATE SCHEMA "{schema}"')
        c.execute(f'SET search_path TO "{schema}"')
        c.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        c.commit()
        yield c
    finally:
        c.rollback()
        c.execute("SET search_path TO public")
        c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        c.commit()
        c.close()
