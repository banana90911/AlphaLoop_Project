"""
description:        시간대 정규화 단일 책임 (UTC 저장 · KST 표시)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import UTC, date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_utc() -> datetime:
    """tz-aware UTC 현재시각."""
    return datetime.now(UTC)


def utc_iso() -> str:
    """UTC ISO8601 문자열(로그·파일명용)."""
    return now_utc().isoformat()


def kst_today() -> date:
    """KST 기준 오늘 날짜."""
    return to_kst(now_utc()).date()


def to_kst(dt: datetime) -> datetime:
    """UTC(또는 naive=UTC 가정) → KST 변환."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(KST)


def kst_day_bounds(day: date) -> tuple[datetime, datetime]:
    """KST 하루(00:00~다음날 00:00)를 UTC 경계 두 개로 바꾼다.

    `timestamptz` 컬럼을 KST 날짜로 거를 때 쓴다 — date와 직접 비교하면 자정이
    UTC로 해석돼 9시간이 밀린다(07-model 공통 규칙: 시각은 UTC, 거래일은 KST).
    """
    start = datetime.combine(day, datetime.min.time(), tzinfo=KST)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def session_label(started_at: datetime | str) -> str:
    """KST 기준 morning(<12시)/afternoon 라벨을 반환한다."""
    if isinstance(started_at, str):
        started_at = datetime.fromisoformat(started_at)
    return "morning" if to_kst(started_at).hour < 12 else "afternoon"
