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
from datetime import datetime, timedelta

import jwt

from config.settings import get_settings
from core.timeutils import now_utc

log = logging.getLogger(__name__)

# 자동 연장 없음 — 만료되면 다시 로그인. 쿠키는 세션 쿠키라 브라우저를 닫아도 사라지지만,
# "마지막 탭 이어서 열기"를 켠 브라우저는 세션 쿠키까지 복원한다. 그래서 브라우저 종료만
# 믿지 않고 시각으로도 끊는다 — 둘 중 먼저 오는 쪽이 만료다.
TOKEN_TTL_HOURS = 12
COOKIE_NAME = "alphaloop_session"
ALGORITHM = "HS256"

# 비밀번호 무차별 대입 차단. 이 문을 잠그는 것이 출입증 유효기간을 줄이는 것보다
# 실제로 크게 안전해진다 — 출입증은 HttpOnly라 화면 코드가 훔쳐갈 수 없지만,
# 로그인 문은 주소만 알면 누구나 두드릴 수 있기 때문이다.
LOCKOUT_THRESHOLD = 5       # 연속 실패 이만큼이면
LOCKOUT_MINUTES = 15        # 이 시간 동안 그 주소를 막는다

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
    """12시간 만료 출입증(JWT)을 발급한다."""
    now = now_utc()
    return jwt.encode(
        {"sub": "owner", "iat": now, "exp": now + timedelta(hours=TOKEN_TTL_HOURS)},
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


# ── 무차별 대입 잠금 ─────────────────────────────────────────────
# 주소별 연속 실패 횟수와 잠금 해제 시각. 프로세스 메모리에만 둔다 —
# DB에 쓰지 않는 것이 8.1의 경계이고, 재시작으로 풀리는 건 감수한다(쓰는 사람이 하나라
# 재시작을 공격자가 일으킬 수 없다). 다만 uvicorn 워커를 여럿 띄우면 워커마다 따로
# 세므로, 이 잠금이 의미를 가지려면 워커는 하나여야 한다.
_attempts: dict[str, tuple[int, datetime]] = {}   # 주소 → (연속 실패, 잠금 해제 시각)


def locked_seconds(source: str) -> int:
    """그 주소가 잠겨 있으면 남은 초를, 아니면 0을 반환한다."""
    entry = _attempts.get(source)
    if entry is None:
        return 0
    _, until = entry
    remaining = (until - now_utc()).total_seconds()
    return max(0, int(remaining))


def record_attempt(ok: bool, source: str) -> None:
    """시도 결과를 세어 임계를 넘으면 그 주소를 잠근다(성공하면 초기화)."""
    if ok:
        _attempts.pop(source, None)
        return
    fails = _attempts.get(source, (0, now_utc()))[0] + 1
    until = now_utc() + timedelta(minutes=LOCKOUT_MINUTES) if fails >= LOCKOUT_THRESHOLD \
        else now_utc()
    _attempts[source] = (fails, until)
    if fails >= LOCKOUT_THRESHOLD:
        # 내가 하지 않은 시도가 여기 보이면 그 자체가 신호다(8.6)
        log.warning("dashboard login LOCKED %s after %d failures for %d minutes",
                    source, fails, LOCKOUT_MINUTES)


def is_configured() -> bool:
    """로그인 준비 여부(해시·서명 열쇠가 둘 다 있는지)를 반환한다."""
    s = get_settings()
    return bool(s.dashboard_password_hash and s.dashboard_token_secret)


def cookie_kwargs() -> dict:
    """출입증 쿠키 설정(HttpOnly)을 반환한다.

    화면과 API가 다른 출처에 있으면(Vercel ↔ NCP) 브라우저는 `SameSite=lax` 쿠키를
    아예 안 보낸다 — 로그인은 되는데 그 다음 조회가 전부 401이 되는 형태로 나타난다.
    그래서 허용 출처가 설정돼 있을 때만 `none`으로 낮추고, 대신 `Secure`를 강제한다.

    `max_age`를 주지 않는 것이 핵심이다 — 그래야 브라우저가 디스크에 저장하지 않고
    창을 닫을 때 버리는 세션 쿠키가 된다. 만료 시각은 출입증 안에도 박혀 있으므로,
    브라우저가 쿠키를 되살려도 서버가 12시간 뒤에 거절한다.
    """
    insecure = os.environ.get("DASHBOARD_INSECURE_COOKIE") == "1"
    cross_site = bool(get_settings().dashboard_allowed_origins) and not insecure
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        # SameSite=none은 Secure 없이는 브라우저가 거부한다 — 둘은 항상 같이 간다
        "samesite": "none" if cross_site else "lax",
        "secure": not insecure,
    }
