"""
description:        거래일·휴장 판정 (달력 1차 + 가짜 KIS client로 2차 확인 검증)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""
from datetime import date, datetime, time

from core.timeutils import KST
from core.trading_days import (
    is_session_open,
    is_trading_day,
    next_trading_day,
    previous_trading_day,
    session_close,
    session_open,
)


def test_weekend_is_not_trading_day():
    assert is_trading_day(date(2026, 8, 29)) is False   # 토
    assert is_trading_day(date(2026, 8, 30)) is False   # 일


def test_weekday_is_trading_day():
    assert is_trading_day(date(2026, 8, 28)) is True    # 금


def test_public_holiday_is_not_trading_day():
    assert is_trading_day(date(2026, 1, 1)) is False    # 신정


def test_previous_trading_day_skips_weekend():
    assert previous_trading_day(date(2026, 8, 29)) == date(2026, 8, 28)


def test_previous_trading_day_excludes_self():
    # 거래일 자신을 넘겨도 그 *이전* 거래일을 준다 — '전일 종가' 기준일 용도
    assert previous_trading_day(date(2026, 8, 28)) == date(2026, 8, 27)


def test_next_trading_day_skips_weekend():
    assert next_trading_day(date(2026, 8, 29)) == date(2026, 8, 31)


def test_session_times_are_kst():
    assert session_open(date(2026, 8, 28)) == time(9, 0)
    assert session_close(date(2026, 8, 28)) == time(15, 30)


def test_session_times_fall_back_on_holiday():
    # 휴장일은 정규 시각을 돌려준다(판정 자체는 is_trading_day가 한다)
    assert session_close(date(2026, 8, 29)) == time(15, 30)


class _FakeKIS:
    """KIS 휴장일 조회 흉내 — opnd_yn만 통제한다."""

    def __init__(self, opened: str | None, raises: bool = False):
        self.opened, self.raises = opened, raises

    def get_holidays(self, day):
        if self.raises:
            raise RuntimeError("모의투자 TR 이 아닙니다")
        if self.opened is None:
            return []
        return [{"bass_dt": day.strftime("%Y%m%d"), "opnd_yn": self.opened}]


def test_kis_overrides_calendar_on_conflict():
    # 달력은 개장이라 하지만 KIS가 임시휴장이라 하면 KIS를 따른다
    assert is_trading_day(date(2026, 8, 28), client=_FakeKIS("N")) is False
    # 반대 방향(달력 휴장 · KIS 개장)도 KIS가 이긴다 — 임시 개장
    assert is_trading_day(date(2026, 8, 29), client=_FakeKIS("Y")) is True


def test_kis_failure_falls_back_to_calendar():
    # 조회가 안 된다고 매매를 멈추면 멀쩡한 날을 통째로 버린다
    assert is_trading_day(date(2026, 8, 28), client=_FakeKIS(None, raises=True)) is True
    assert is_trading_day(date(2026, 8, 29), client=_FakeKIS(None, raises=True)) is False


def test_kis_silent_when_date_absent():
    # 응답에 해당 날짜가 없으면 판정 불가 → 달력 유지
    assert is_trading_day(date(2026, 8, 28), client=_FakeKIS(None)) is True


def test_is_session_open_within_hours():
    mid = datetime(2026, 8, 28, 14, 30, tzinfo=KST)
    assert is_session_open(mid) is True


def test_is_session_open_false_after_close():
    after = datetime(2026, 8, 28, 16, 0, tzinfo=KST)
    assert is_session_open(after) is False


def test_is_session_open_false_on_weekend():
    sat = datetime(2026, 8, 29, 11, 0, tzinfo=KST)
    assert is_session_open(sat) is False
