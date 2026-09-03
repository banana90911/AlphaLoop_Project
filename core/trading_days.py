"""
description:        거래일·휴장 판정 (달력 1차 + KIS 조회 2차 확인)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date, datetime, time
from functools import lru_cache
from typing import Any

from core.timeutils import kst_today, to_kst

MARKET = "XKRX"
# 정규장(KST). 달력이 주는 값을 우선 쓰고, 이 값은 달력을 못 읽을 때의 기준이다.
REGULAR_OPEN = time(9, 0)
REGULAR_CLOSE = time(15, 30)


@lru_cache(maxsize=1)
def _calendar() -> Any:
    """XKRX 달력을 1회만 생성해 캐시한다."""
    import exchange_calendars as xc

    return xc.get_calendar(MARKET)


def is_trading_day(day: date | None = None, *, client: Any = None) -> bool:
    """그날 한국 주식시장이 열리는지 판정한다(달력 + 선택적 KIS 확인)."""
    day = day or kst_today()
    by_calendar = _calendar().is_session(day.isoformat())
    if client is None:
        return by_calendar
    by_kis = _kis_is_open(client, day)
    return by_calendar if by_kis is None else by_kis


def _kis_is_open(client: Any, day: date) -> bool | None:
    """KIS 국내휴장일 조회. 판정 불가면 None."""
    try:
        rows = client.get_holidays(day)
    except Exception:                      # 조회 실패가 매매 판단을 멈추면 안 된다
        return None
    target = day.strftime("%Y%m%d")
    for row in rows:
        if row.get("bass_dt") == target:
            return row.get("opnd_yn") == "Y"      # 개장일 여부
    return None


def previous_trading_day(day: date | None = None) -> date:
    """직전 거래일(day 자신이 거래일이어도 그 이전 거래일)을 반환한다."""
    day = day or kst_today()
    cal = _calendar()
    session = cal.date_to_session(day.isoformat(), direction="previous")
    if session.date() == day:
        session = cal.previous_session(session)
    return session.date()


def next_trading_day(day: date | None = None) -> date:
    """다음 거래일(day 자신이 거래일이어도 그 이후 거래일)을 반환한다."""
    day = day or kst_today()
    cal = _calendar()
    session = cal.date_to_session(day.isoformat(), direction="next")
    if session.date() == day:
        session = cal.next_session(session)
    return session.date()


def trading_days_between(start: date, end: date) -> int:
    """start(제외)부터 end(포함)까지의 거래일 수를 센다.

    보유일수의 단위다 — 설계의 청산 규칙은 전부 거래일 기준이라(06-sizing 6.2
    "20거래일 초과"), 달력일로 세면 주말·연휴만큼 일찍 청산된다.
    """
    if end <= start:
        return 0
    sessions = _calendar().sessions_in_range(start.isoformat(), end.isoformat())
    # sessions_in_range는 양끝을 포함하므로 start가 거래일이면 그 하루를 뺀다
    return max(0, len(sessions) - (1 if _calendar().is_session(start.isoformat()) else 0))


def session_close(day: date | None = None) -> time:
    """그날 정규장 종료 시각(KST)을 반환한다."""
    day = day or kst_today()
    cal = _calendar()
    if not cal.is_session(day.isoformat()):
        return REGULAR_CLOSE
    return to_kst(cal.session_close(day.isoformat()).to_pydatetime()).time()


def session_open(day: date | None = None) -> time:
    """그날 정규장 시작 시각(KST)을 반환한다."""
    day = day or kst_today()
    cal = _calendar()
    if not cal.is_session(day.isoformat()):
        return REGULAR_OPEN
    return to_kst(cal.session_open(day.isoformat()).to_pydatetime()).time()


def is_session_open(now: datetime | None = None) -> bool:
    """지금 정규장이 열려 있는지 판정한다."""
    now_kst = to_kst(now) if now else to_kst(datetime.now().astimezone())
    day = now_kst.date()
    if not is_trading_day(day):
        return False
    return session_open(day) <= now_kst.time() < session_close(day)
