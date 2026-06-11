"""사이클 상태머신·idempotency (0-B 게이트, 11-2.1)."""
from memory import journal
from memory.db import init_db
from pipeline.trading_cycle import run_cycle


def test_cycle_reaches_recorded(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    cid = run_cycle(conn)
    status = conn.execute("SELECT status FROM cycles WHERE cycle_id=?", (cid,)).fetchone()["status"]
    assert status == "recorded"
    conn.close()


def test_recover_marks_pending_failed(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    journal.create_cycle(conn, "STUCK", "scheduled")  # intent로 방치(프로세스 사망 모사)
    recovered = journal.recover_pending_cycles(conn)
    assert recovered == ["STUCK"]
    status = conn.execute("SELECT status FROM cycles WHERE cycle_id='STUCK'").fetchone()["status"]
    assert status == "failed"
    conn.close()


def test_recover_noop_when_clean(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    run_cycle(conn)  # 완료 사이클만 존재
    assert journal.recover_pending_cycles(conn) == []
    conn.close()


def test_advance_status_rejects_unknown(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    journal.create_cycle(conn, "C1", "scheduled")
    try:
        journal.advance_status(conn, "C1", "bogus")
        raise AssertionError("unknown status가 허용됨")
    except ValueError:
        pass
    conn.close()
