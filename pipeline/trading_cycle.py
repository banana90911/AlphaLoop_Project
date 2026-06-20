"""매매 사이클 8단계 오케스트레이션 (03-arch 3.1).

상태머신(`intent`→`ordering`→`recorded`)을 축으로, 부품이 준비된 단계부터 채운다.
1단계(후보 선별)는 `market_data`가 주어지면 작동하고, 없으면 빈 사이클로 흐른다(상태머신만).
2~8단계(데이터·기억·LLM·리스크·주문·기록)는 후속 Phase에서 연결한다.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd

from core.timeutils import now_utc
from memory import journal
from pipeline import screening


def new_cycle_id(now=None) -> str:
    """timestamp 기반 cycle_id 발급(06 cycles)."""
    return (now or now_utc()).strftime("%Y%m%dT%H%M%S%fZ")


def run_cycle(
    conn: sqlite3.Connection,
    trigger_type: str = "scheduled",
    trigger_event_id: str | None = None,
    *,
    market_data: dict[str, pd.DataFrame] | None = None,
    holdings: tuple[str, ...] = (),
    asof: date | None = None,
    min_value_traded: float | None = None,
) -> str:
    """한 사이클 실행. 반환: cycle_id.

    market_data(종목별 OHLCV+수급)가 있으면 1단계 후보 선별을 수행한다. 이벤트 트리거
    사이클은 스크리닝을 건너뛰고 워치리스트=보유로 좁힌다(3.1).
    """
    cycle_id = new_cycle_id()
    journal.create_cycle(conn, cycle_id, trigger_type, trigger_event_id)

    # 1단계: 후보 선별 → 워치리스트
    if trigger_type == "event":
        watchlist = list(holdings)                       # 이벤트는 보유 방어 전용
    elif market_data:
        wl = screening.select_watchlist(
            market_data, holdings=holdings, asof=asof,
            min_value_traded=min_value_traded,
        )
        watchlist = list(wl.index)
    else:
        watchlist = list(holdings)                       # 빈 사이클(데이터 미주입)

    # 2~6단계: 데이터 수집 → 기억 검색 → LLM 분석·결정 → 리스크 검증 (후속 Phase, watchlist 입력)

    journal.advance_status(conn, cycle_id, "ordering")
    # 7단계: 주문 송출 (후속 Phase)

    journal.advance_status(conn, cycle_id, "recorded")
    # 8단계: 기록·정합성 (후속 Phase)
    _ = watchlist  # 후속 단계 입력(현재는 선별까지만)
    return cycle_id
