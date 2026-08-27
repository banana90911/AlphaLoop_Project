"""거래일·휴장·반장 판정 (10-ops 10.8 · 03-arch 3.3).

사이클이 도는 날인지, 오늘 장이 언제 닫는지를 정하는 단일 책임. 이 판정이 없으면
공휴일에도 사이클이 돌아 빈 조회로 실패 기록만 쌓인다.

**두 겹으로 확인한다.** 1차는 `exchange_calendars`의 XKRX 달력(패키지 의존, 미리 알 수
있다), 2차는 KIS 영업일 조회(실제 거래소 응답). 둘이 어긋나면 KIS를 따르고 알림을
보낸다 — 임시 휴장·연장은 달력 패키지가 늦게 반영한다.

시각 비교는 전부 KST 기준이다(`core.timeutils`). 시계가 1분 어긋나면 종가 컷오프를
오판해 잘못된 데이터로 결정하므로, 판정에 쓰는 시각은 한 군데서만 만든다.

미구현 — 아래 함수는 전부 뼈대다.
"""
from __future__ import annotations

from datetime import date, time


def is_trading_day(day: date | None = None) -> bool:
    """그날 한국 주식시장이 열리는가. 주말·공휴일·임시휴장이면 False."""
    raise NotImplementedError


def previous_trading_day(day: date | None = None) -> date:
    """직전 거래일. 점수 계산의 '전일 종가' 기준일을 정할 때 쓴다."""
    raise NotImplementedError


def session_close(day: date | None = None) -> time:
    """그날 정규장 종료 시각(KST). 반장이면 12:30, 평소 15:30."""
    raise NotImplementedError


def is_half_day(day: date | None = None) -> bool:
    """반장(조기 폐장)인가 — 연말 폐장일·수능일 등. 사이클 시각 14:30이 장 마감 뒤가 된다."""
    raise NotImplementedError
