"""사이클·결정 적재·조회 (06 객체). 0-B는 `cycles` 상태머신, Phase 4는 `decisions` 적재.

trades·outcomes·agent_predictions 적재는 후속(주문 집행·체결·학습 연결 시).
idempotency: 사이클은 `intent`→`ordering`→`recorded` 상태머신을 따르며,
미완(`intent`/`ordering`)으로 남은 사이클은 시작 시 복구한다(11-2.1).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from core.schemas import ProposedOrder
from core.timeutils import utc_iso

CYCLE_STATES = ("intent", "ordering", "recorded", "failed")

# OrderAction(buy/add/hold/trim/sell) → decisions.action·side (06 schema CHECK 제약).
# hold는 주문이 없어 적재하지 않는다(무거래 기록은 후속 shadow/no_trade).
_ACTION_MAP = {
    "buy": ("buy", "buy"),       # 신규 진입
    "add": ("buy", "buy"),       # 추가 매수(side는 buy)
    "sell": ("sell", "sell"),    # 전량 청산
    "trim": ("trim", "sell"),    # 부분 청산(side는 sell)
}


def create_cycle(
    conn: sqlite3.Connection,
    cycle_id: str,
    trigger_type: str,
    trigger_event_id: str | None = None,
) -> None:
    """`intent` 상태로 사이클 1행 생성(모든 산출물의 부모 키)."""
    conn.execute(
        "INSERT INTO cycles(cycle_id, status, trigger_type, trigger_event_id, started_at) "
        "VALUES(?, ?, ?, ?, ?)",
        (cycle_id, "intent", trigger_type, trigger_event_id, utc_iso()),
    )
    conn.commit()


def advance_status(conn: sqlite3.Connection, cycle_id: str, status: str) -> None:
    """상태 전이. `recorded`/`failed`면 finished_at 기록."""
    if status not in CYCLE_STATES:
        raise ValueError(f"unknown cycle status: {status}")
    if status in ("recorded", "failed"):
        conn.execute(
            "UPDATE cycles SET status=?, finished_at=? WHERE cycle_id=?",
            (status, utc_iso(), cycle_id),
        )
    else:
        conn.execute("UPDATE cycles SET status=? WHERE cycle_id=?", (status, cycle_id))
    conn.commit()


def record_decisions(
    conn: sqlite3.Connection,
    cycle_id: str,
    orders: Iterable[ProposedOrder],
    *,
    stops: dict[str, float] | None = None,
    source: str = "paper",
    decided_at: str | None = None,
) -> list[str]:
    """결정 제안(buy/add/sell/trim)을 `decisions`에 적재 (8단계 기록). 반환: decision_id 목록.

    hold는 주문이 없어 건너뛴다. buy 계열의 stop_loss는 stops(code→손절가, 집행 계획)에서
    채운다. decision_id는 cycle_id+종목+action 결정론 키(사이클 내 유일)로 재현 가능하게.
    """
    stops = stops or {}
    ts = decided_at or utc_iso()
    ids: list[str] = []
    for o in orders:
        mapped = _ACTION_MAP.get(str(o.action))
        if mapped is None:                           # hold 등은 적재 생략
            continue
        action, side = mapped
        did = f"{cycle_id}_{o.code}_{action}"
        conn.execute(
            "INSERT INTO decisions(decision_id, cycle_id, symbol, action, side, "
            "qty_risk_budget, entry_thesis, stop_loss, dissent_addressed, source, decided_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (did, cycle_id, o.code, action, side, o.risk_budget, o.thesis or None,
             stops.get(o.code), str(o.dissent_addressed), source, ts),
        )
        ids.append(did)
    conn.commit()
    return ids


def recover_pending_cycles(conn: sqlite3.Connection) -> list[str]:
    """시작 시 미완(intent/ordering) 사이클을 failed로 마감하고 그 id 목록 반환(11-2.1).

    프로세스가 사이클 도중 죽어도 다음 실행이 깨끗한 상태에서 시작하게 한다.
    """
    rows = conn.execute(
        "SELECT cycle_id FROM cycles WHERE status IN ('intent','ordering')"
    ).fetchall()
    pending = [r[0] for r in rows]
    for cid in pending:
        advance_status(conn, cid, "failed")
    return pending
