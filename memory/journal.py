"""사이클 적재·조회 (06 객체). 0-B는 `cycles` 상태머신 중심.

다른 객체(decisions·trades·outcomes…) 적재는 후속 Phase에서 추가한다.
idempotency: 사이클은 `intent`→`ordering`→`recorded` 상태머신을 따르며,
미완(`intent`/`ordering`)으로 남은 사이클은 시작 시 복구한다(11-2.1).
"""
from __future__ import annotations

import sqlite3

from core.timeutils import utc_iso

CYCLE_STATES = ("intent", "ordering", "recorded", "failed")


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
