"""
description:        DB 테스트 공통 픽스처 — 테스트마다 임시 스키마 하나
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from config.settings import get_settings
from memory.db import SCHEMA_PATH, connect
from ops import heartbeat, notify

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
    """테스트용 DSN — 환경변수 → pgserver → 설정 순으로 찾는다."""
    return os.environ.get("ALPHALOOP_TEST_DSN") or _embedded_dsn() or get_settings().db_dsn


@pytest.fixture(autouse=True)
def no_external_alerts(monkeypatch):
    """알림·헬스체크가 실제로 나가지 않게 막는다.

    `.env`에 진짜 Discord 웹훅과 헬스체크 주소가 들어 있어, 사이클 테스트가
    SafeStop을 모사할 때마다 운영 채널로 경보가 날아간다. 테스트는 네트워크를
    건드리지 않는다.
    """
    monkeypatch.setattr(notify, "send", lambda *a, **k: False)
    monkeypatch.setattr(heartbeat, "_ping", lambda *a, **k: False)
    monkeypatch.setattr(heartbeat, "ping_safe_stop", lambda *a, **k: False)


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
