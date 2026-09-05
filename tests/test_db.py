"""
description:        DB 초기화·제약·멱등성 (0-B 게이트). 표·컬럼 이름은 07-model 그대로 snake_case.
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from memory.db import SCHEMA_PATH

CATALOG = {
    "symbols", "symbol_states", "daily_bars", "daily_flows", "corporate_actions",
    "market_indices", "ingest_runs",
    "cycles", "account_snapshots", "daily_scores", "cycle_scores", "decisions", "risk_checks",
    "orders", "cash_flows", "positions", "outcomes",
    "safe_stop_events",
}


def _tables(conn) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema()"
    ).fetchall()
    return {r["table_name"] for r in rows}


def test_schema_creates_core_tables(conn):
    """07-model 표 카탈로그 18개가 전부 생성된다."""
    assert CATALOG <= _tables(conn)


def test_money_columns_are_numeric(conn):
    """금액·가격은 numeric이라야 반올림 오차가 자본곡선에 누적되지 않는다(07-model)."""
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'outcomes'"
    ).fetchall()
    types = {r["column_name"]: r["data_type"] for r in rows}
    assert types["entry_price"] == "numeric" and types["net_profit_loss"] == "numeric"
    assert types["return_percent"] == "double precision"    # 비율은 부동소수
    assert types["quantity"] == "integer"
    assert types["closed_date_time"] == "timestamp with time zone"


def test_foreign_keys_are_enforced(conn):
    """없는 사이클을 참조하는 결정은 거부된다(FK 무결성)."""
    import psycopg

    try:
        conn.execute(
            'INSERT INTO decisions(decision_id, cycle_id, symbol_id, action, '
            'reason, decided_date_time) '
            "VALUES('D1', 'NOPE', '005930', 'buy', 'entryThreshold', now())"
        )
        raise AssertionError("없는 CycleId 참조가 허용됨")
    except psycopg.errors.ForeignKeyViolation:
        conn.rollback()


def test_check_constraints_reject_unknown_values(conn):
    """CHECK 제약이 설계에 없는 값을 막는다(Decisions.Action)."""
    import psycopg

    conn.execute(
        'INSERT INTO cycles(cycle_id, trade_date, status, mode, started_date_time) '
        "VALUES('C1', DATE '2024-01-02', 'intent', 'paper', now())"
    )
    try:
        conn.execute(
            'INSERT INTO decisions(decision_id, cycle_id, symbol_id, action, '
            'reason, decided_date_time) '
            "VALUES('D1', 'C1', '005930', 'exitPartial', 'entryThreshold', now())"
        )
        raise AssertionError("설계에서 뺀 exitPartial이 허용됨")
    except psycopg.errors.CheckViolation:
        conn.rollback()


def test_schema_is_idempotent(conn):
    """재적용해도 에러 없어야(IF NOT EXISTS) — 표 수도 그대로."""
    before = _tables(conn)
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    assert _tables(conn) == before
