"""
description:        SafeStop 조회·해제 CLI (05-risk 5.4 — 사람 개입 필수 경로)
author:             siheon jung
created date:       2026/09/07
last modified date: 2026/09/07
remarks:            해제는 되돌릴 수 없는 판단이다. 원인을 확인한 사람이
                    사유를 남기고 직접 푼다 — 자동 해제 경로를 두지 않는다.
"""

import argparse

from memory import journal
from memory.db import connect


def cmd_list(conn) -> int:
    """미해제 SafeStop을 보여준다."""
    rows = conn.execute(
        'SELECT event_id, cause, occurred_date_time, cycle_id '
        'FROM safe_stop_events WHERE released_date_time IS NULL '
        'ORDER BY occurred_date_time DESC'
    ).fetchall()
    if not rows:
        print("미해제 SafeStop이 없다 — 매매가 열려 있다")
        return 0
    print(f"미해제 SafeStop {len(rows)}건 — 해제 전까지 신규 진입이 막힌다\n")
    for r in rows:
        print(f"  EventId : {r['event_id']}")
        print(f"  원인    : {r['cause']}")
        print(f"  발생    : {r['occurred_date_time']}")
        print(f"  사이클  : {r['cycle_id']}\n")
    return 0


def cmd_release(conn, *, event_id: str, by: str, reason: str) -> int:
    """원인을 확인한 사람이 사유를 남기고 해제한다."""
    row = conn.execute(
        'SELECT cause, released_date_time FROM safe_stop_events WHERE event_id=%s',
        (event_id,),
    ).fetchone()
    if row is None:
        print(f"그런 EventId가 없다: {event_id}")
        return 1
    if row["released_date_time"] is not None:
        print(f"이미 해제된 건이다({row['released_date_time']})")
        return 1

    journal.release_safe_stop(conn, event_id, released_by=by, reason=reason)
    print(f"해제 완료: {event_id} · {row['cause']}")
    print(f"  해제자  : {by}")
    print(f"  사유    : {reason}")
    left = conn.execute(
        'SELECT COUNT(*) AS c FROM safe_stop_events WHERE released_date_time IS NULL'
    ).fetchone()["c"]
    print(f"  남은 미해제: {left}건")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description="AlphaLoop SafeStop 조회·해제")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="미해제 SafeStop 보기")
    p_rel = sub.add_parser("release", help="원인을 확인한 뒤 해제")
    p_rel.add_argument("--id", required=True, help="EventId")
    p_rel.add_argument("--by", required=True, help="해제자(사후 감사에 남는다)")
    p_rel.add_argument("--reason", required=True, help="해제 사유(무엇을 확인했는가)")
    args = ap.parse_args(argv)

    conn = connect()
    try:
        if args.cmd == "list":
            return cmd_list(conn)
        return cmd_release(conn, event_id=args.id, by=args.by, reason=args.reason)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
