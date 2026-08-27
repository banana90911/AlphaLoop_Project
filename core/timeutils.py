"""시간대 정규화 단일 책임 (10-ops 10.8).

내부 저장·연산은 **UTC**, 표시·세션 판정은 **KST**. naive datetime은 쓰지 않는다.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_utc() -> datetime:
    """tz-aware UTC 현재시각."""
    return datetime.now(UTC)


def utc_iso() -> str:
    """UTC ISO8601 문자열(로그·파일명용). DB의 timestamptz 컬럼에는 datetime을 그대로 넣는다."""
    return now_utc().isoformat()


def kst_today() -> date:
    """KST 기준 오늘 날짜 — 일별 집계의 거래일(TradeDate)은 이 값이 기준이다(07-model)."""
    return to_kst(now_utc()).date()


def to_kst(dt: datetime) -> datetime:
    """UTC(또는 naive=UTC 가정) → KST."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(KST)


def session_label(started_at: datetime | str) -> str:
    """KST 기준 morning(<12시)/afternoon. cycle.started_at에서 유도(06 decisions)."""
    if isinstance(started_at, str):
        started_at = datetime.fromisoformat(started_at)
    return "morning" if to_kst(started_at).hour < 12 else "afternoon"
