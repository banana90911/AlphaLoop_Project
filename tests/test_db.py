"""DB 초기화·PRAGMA·멱등성 (0-B 게이트)."""
from memory.db import init_db


def test_schema_creates_core_tables(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"cycles", "decisions", "trades", "outcomes", "agent_predictions", "lessons"} <= tables
    conn.close()


def test_wal_and_foreign_keys(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_schema_is_idempotent(tmp_path):
    db = str(tmp_path / "t.db")
    init_db(db).close()
    init_db(db).close()  # 재적용해도 에러 없어야(IF NOT EXISTS)


def test_calibration_view_exists(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert "calibration" in views
    conn.close()
