"""
description:        로그인·출입증 검증 (비밀번호 1개 + JWT 쿠키)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import hashlib
import hmac
import logging
import os
import secrets
from datetime import timedelta

import jwt

from config.settings import get_settings
from core.timeutils import now_utc

log = logging.getLogger(__name__)

TOKEN_TTL_DAYS = 7          # 자동 연장 없음 — 만료되면 다시 로그인
COOKIE_NAME = "alphaloop_session"
ALGORITHM = "HS256"

# scrypt 매개변수 — 저장 형식에 함께 적어 나중에 값을 올려도 옛 해시를 읽을 수 있다.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def make_hash(password: str) -> str:
    """비밀번호를 `scrypt$N$r$p$salt$hash` 문자열로 해시한다."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                        p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(raw: str) -> bool:
    """입력한 비밀번호를 저장된 해시와 상수시간 비교로 대조한다."""
    stored = get_settings().dashboard_password_hash
    if not stored:
        log.error("dashboard_password_hash 미설정 — 로그인을 받을 수 없다")
        return False
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            raise ValueError(scheme)
        dk = hashlib.scrypt(raw.encode(), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=len(hash_hex) // 2)
    except (ValueError, TypeError) as e:
        log.error("저장된 해시 형식이 잘못됐다: %s", type(e).__name__)
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def _secret() -> str:
    """설정에서 토큰 서명 열쇠를 읽는다(없으면 에러)."""
    s = get_settings().dashboard_token_secret
    if not s:
        raise RuntimeError("dashboard_token_secret 미설정 — 출입증을 발급할 수 없다")
    return s


def issue_token() -> str:
    """7일 만료 출입증(JWT)을 발급한다."""
    now = now_utc()
    return jwt.encode(
        {"sub": "owner", "iat": now, "exp": now + timedelta(days=TOKEN_TTL_DAYS)},
        _secret(), algorithm=ALGORITHM,
    )


def verify_token(token: str | None) -> bool:
    """출입증이 유효한지 검증한다(없거나 만료·서명 불일치면 False)."""
    if not token:
        return False
    try:
        jwt.decode(token, _secret(), algorithms=[ALGORITHM])
        return True
    except jwt.PyJWTError:
        return False


def log_attempt(ok: bool, source: str = "") -> None:
    """로그인 시도를 로그 파일에만 남긴다."""
    log.info("dashboard login %s%s", "success" if ok else "FAILURE",
             f" from {source}" if source else "")


def is_configured() -> bool:
    """로그인 준비 여부(해시·서명 열쇠가 둘 다 있는지)를 반환한다."""
    s = get_settings()
    return bool(s.dashboard_password_hash and s.dashboard_token_secret)


def cookie_kwargs() -> dict:
    """출입증 쿠키 설정(HttpOnly)을 반환한다."""
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": os.environ.get("DASHBOARD_INSECURE_COOKIE") != "1",
        "max_age": TOKEN_TTL_DAYS * 86400,
    }
