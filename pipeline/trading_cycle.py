"""매매 사이클 9단계 오케스트레이션 (03-arch 3.1).

0-B 단계에서는 **빈 사이클**: 데이터 수집·LLM 결정·주문 없이 상태머신만 돈다
(`intent`→`ordering`→`recorded`). 2~8단계는 후속 Phase에서 채운다.
"""
from __future__ import annotations

import sqlite3

from core.timeutils import now_utc
from memory import journal


def new_cycle_id(now=None) -> str:
    """timestamp 기반 cycle_id 발급(06 cycles)."""
    return (now or now_utc()).strftime("%Y%m%dT%H%M%S%fZ")


def run_cycle(
    conn: sqlite3.Connection,
    trigger_type: str = "scheduled",
    trigger_event_id: str | None = None,
) -> str:
    """한 사이클 실행. 반환: cycle_id."""
    cycle_id = new_cycle_id()
    journal.create_cycle(conn, cycle_id, trigger_type, trigger_event_id)

    # 2~7단계: 데이터 수집 → 후보 선별 → LLM 분석·결정 (후속 Phase)

    journal.advance_status(conn, cycle_id, "ordering")
    # 8단계: 주문 송출·체결 기록 (후속 Phase)

    journal.advance_status(conn, cycle_id, "recorded")
    # 9단계: 마감·정합성 (후속 Phase)
    return cycle_id
