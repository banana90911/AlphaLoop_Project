"""매매 사이클 단일 진입점 (외부 스케줄러가 호출 — 03-arch 3.1).

trigger_type으로 정기(scheduled)/이벤트(event)를 구분한다.
실행 전 미완 사이클을 복구해 멱등성을 보장한다(11-2.1).
"""
from __future__ import annotations

import argparse

from memory import journal
from memory.db import init_db
from pipeline.trading_cycle import run_cycle


def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaLoop 매매 사이클")
    parser.add_argument("--trigger", choices=["scheduled", "event"], default="scheduled")
    args = parser.parse_args()

    conn = init_db()
    recovered = journal.recover_pending_cycles(conn)
    if recovered:
        print(f"미완 사이클 복구(failed): {recovered}")

    cycle_id = run_cycle(conn, trigger_type=args.trigger)
    status = conn.execute(
        "SELECT status FROM cycles WHERE cycle_id=?", (cycle_id,)
    ).fetchone()["status"]
    print(f"사이클 {cycle_id} → {status}")
    conn.close()


if __name__ == "__main__":
    main()
