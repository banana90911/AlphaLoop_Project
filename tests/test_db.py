"""DB 초기화·제약·멱등성 (0-B 게이트). 표·컬럼 이름은 07-model 그대로 PascalCase."""
from memory.db import SCHEMA_PATH

CATALOG = {
    "Symbols", "SymbolStates", "DailyBars", "DailyFlows", "CorporateActions",
    "MarketIndices", "IngestRuns",
    "Cycles", "AccountSnapshots", "DailyScores", "CycleScores", "Decisions", "RiskChecks",
    "Orders", "Positions", "Outcomes",
    "SafeStopEvents",
}


def _tables(conn) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema()"
    ).fetchall()
    return {r["table_name"] for r in rows}


def test_schema_creates_core_tables(conn):
    """07-model 표 카탈로그 17개가 전부 생성된다."""
    assert CATALOG <= _tables(conn)


def test_identifiers_keep_pascal_case(conn):
    """컬럼명이 소문자로 접히지 않는다 — DDL이 큰따옴표로 감싼 결과."""
    cols = {
        r["column_name"]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'Symbols'"
        ).fetchall()
    }
    assert {"SymbolId", "SecurityType", "LastUpdateDateTime"} <= cols
    assert "symbolid" not in cols


def test_money_columns_are_numeric(conn):
    """금액·가격은 numeric이라야 반올림 오차가 자본곡선에 누적되지 않는다(07-model)."""
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'Outcomes'"
    ).fetchall()
    types = {r["column_name"]: r["data_type"] for r in rows}
    assert types["EntryPrice"] == "numeric" and types["NetProfitLoss"] == "numeric"
    assert types["ReturnPercent"] == "double precision"    # 비율은 부동소수
    assert types["Quantity"] == "integer"
    assert types["ClosedDateTime"] == "timestamp with time zone"


def test_foreign_keys_are_enforced(conn):
    """없는 사이클을 참조하는 결정은 거부된다(FK 무결성)."""
    import psycopg

    try:
        conn.execute(
            'INSERT INTO "Decisions"("DecisionId", "CycleId", "SymbolId", "Action", '
            '"Reason", "DecidedDateTime") '
            "VALUES('D1', 'NOPE', '005930', 'buy', 'entryThreshold', now())"
        )
        raise AssertionError("없는 CycleId 참조가 허용됨")
    except psycopg.errors.ForeignKeyViolation:
        conn.rollback()


def test_check_constraints_reject_unknown_values(conn):
    """CHECK 제약이 설계에 없는 값을 막는다(Decisions.Action)."""
    import psycopg

    conn.execute(
        'INSERT INTO "Cycles"("CycleId", "TradeDate", "Status", "Mode", "StartedDateTime") '
        "VALUES('C1', DATE '2024-01-02', 'intent', 'paper', now())"
    )
    try:
        conn.execute(
            'INSERT INTO "Decisions"("DecisionId", "CycleId", "SymbolId", "Action", '
            '"Reason", "DecidedDateTime") '
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
