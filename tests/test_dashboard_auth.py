"""
description:        대시보드 로그인·출입증 검증 (08-dashboard 8.6)
author:             siheon jung
created date:       2026/08/31
last modified date: 2026/08/31
remarks:
"""

from datetime import timedelta

import jwt
import pytest

from config.settings import Settings
from core.timeutils import now_utc
from dashboard import auth

_SECRET = "test-signing-key-do-not-use-in-production"


@pytest.fixture
def configured(monkeypatch):
    """비밀번호 해시와 서명 열쇠가 설정된 상태를 만든다."""
    stored = auth.make_hash("hunter2")
    settings = Settings(dashboard_password_hash=stored, dashboard_token_secret=_SECRET)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    return settings


# ── 비밀번호: 원문을 저장하지 않는다 ──────────────────────────────
def test_hash_is_not_reversible():
    h = auth.make_hash("hunter2")
    assert "hunter2" not in h
    assert h.startswith("scrypt$")


def test_hash_is_salted():
    # 같은 비밀번호라도 매번 다른 해시 — 무지개표 대비
    assert auth.make_hash("hunter2") != auth.make_hash("hunter2")


def test_correct_password_verifies(configured):
    assert auth.verify_password("hunter2")


def test_wrong_password_rejected(configured):
    assert not auth.verify_password("hunter3")
    assert not auth.verify_password("")


def test_unconfigured_password_rejects_everything(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: Settings())
    assert not auth.verify_password("hunter2")
    assert not auth.is_configured()


def test_malformed_stored_hash_rejects(monkeypatch):
    monkeypatch.setattr(
        auth, "get_settings",
        lambda: Settings(dashboard_password_hash="쓰레기값", dashboard_token_secret=_SECRET),
    )
    assert not auth.verify_password("hunter2")


def test_unknown_scheme_rejects(monkeypatch):
    monkeypatch.setattr(
        auth, "get_settings",
        lambda: Settings(dashboard_password_hash="md5$1$1$1$aa$bb",
                         dashboard_token_secret=_SECRET),
    )
    assert not auth.verify_password("hunter2")


# ── 출입증: 12시간 만료·서명 검증 ─────────────────────────────────
def test_issued_token_verifies(configured):
    assert auth.verify_token(auth.issue_token())


def test_token_expires_in_twelve_hours(configured):
    payload = jwt.decode(auth.issue_token(), _SECRET, algorithms=[auth.ALGORITHM])
    life = payload["exp"] - payload["iat"]
    assert life == auth.TOKEN_TTL_HOURS * 3600 == 12 * 3600


def test_expired_token_rejected(configured):
    past = now_utc() - timedelta(hours=13)
    stale = jwt.encode({"sub": "owner", "iat": past, "exp": past + timedelta(hours=12)},
                       _SECRET, algorithm=auth.ALGORITHM)
    assert not auth.verify_token(stale)


def test_token_signed_with_other_key_rejected(configured):
    forged = jwt.encode(
        {"sub": "owner", "iat": now_utc(), "exp": now_utc() + timedelta(days=1)},
        "another-signing-key-at-least-32-bytes-long", algorithm=auth.ALGORITHM,
    )
    assert not auth.verify_token(forged)


def test_missing_and_garbage_tokens_rejected(configured):
    assert not auth.verify_token(None)
    assert not auth.verify_token("")
    assert not auth.verify_token("not-a-jwt")


def test_rotating_secret_invalidates_all_tokens(configured, monkeypatch):
    # 열쇠를 바꾸면 발급된 출입증이 한꺼번에 무효가 된다(8.6 전체 차단 수단)
    token = auth.issue_token()
    monkeypatch.setattr(
        auth, "get_settings",
        lambda: Settings(dashboard_password_hash=configured.dashboard_password_hash,
                         dashboard_token_secret="rotated-signing-key-at-least-32-bytes"),
    )
    assert not auth.verify_token(token)


def test_issue_without_secret_raises(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: Settings())
    with pytest.raises(RuntimeError):
        auth.issue_token()


# ── 쿠키: 화면 코드가 읽을 수 없어야 한다 ─────────────────────────
def test_cookie_is_httponly_and_secure(monkeypatch):
    monkeypatch.delenv("DASHBOARD_INSECURE_COOKIE", raising=False)
    kw = auth.cookie_kwargs()
    assert kw["httponly"] is True          # JS가 읽지 못한다
    assert kw["secure"] is True


def test_cookie_is_session_scoped(monkeypatch):
    """max_age가 없어야 브라우저가 디스크에 저장하지 않고 창을 닫을 때 버린다(8.6)."""
    monkeypatch.delenv("DASHBOARD_INSECURE_COOKIE", raising=False)
    kw = auth.cookie_kwargs()
    assert "max_age" not in kw
    assert "expires" not in kw


def test_insecure_cookie_only_when_explicitly_opted_in(monkeypatch):
    monkeypatch.setenv("DASHBOARD_INSECURE_COOKIE", "1")
    assert auth.cookie_kwargs()["secure"] is False


# ── 로그인 기록은 DB가 아니라 로그로 (8.6) ────────────────────────
def test_login_attempts_are_logged(caplog):
    with caplog.at_level("INFO"):
        auth.log_attempt(True, "1.2.3.4")
        auth.log_attempt(False, "5.6.7.8")
    text = caplog.text
    assert "success" in text and "FAILURE" in text
