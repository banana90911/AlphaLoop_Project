"""
description:        exec/orders.py — 신규 진입 송출·체결 적재(5~6단계). FakeBroker로 흐름 검증.
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from exec.orders import ENTRY_ORD_DVSN, Fill, execute_entries
from memory import journal
from pipeline.cycle import PlannedOrder


class FakeBroker:
    """place_entry를 흉내내는 가짜 집행 채널 — 종목별 Fill을 미리 지정."""

    def __init__(self, fills: dict[str, Fill] | None = None) -> None:
        self.fills = fills or {}
        self.calls: list[dict] = []
        self.stops: list[dict] = []

    def place_entry(self, *, code, qty, price, ord_dvsn, client_order_id) -> Fill:
        self.calls.append(
            {"code": code, "qty": qty, "price": price,
             "ord_dvsn": ord_dvsn, "coid": client_order_id}
        )
        return self.fills.get(code, Fill(qty, float(price), "filled"))

    def place_stop(self, *, code, qty, trigger_price, limit_price, client_order_id) -> Fill:
        self.stops.append(
            {"code": code, "qty": qty, "trigger": trigger_price, "coid": client_order_id}
        )
        return Fill(0, None, "submitted", "STOP1")


def _count(conn, table: str, where: str = "") -> int:
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table} {where}").fetchone()["c"]


def _setup(conn, cycle_id="CY1"):
    journal.create_cycle(conn, cycle_id)
    return conn


def test_entry_fill_records_order_and_position(conn):
    _setup(conn)
    planned = [PlannedOrder("005930", 3, 70000.0, 65000.0)]
    fb = FakeBroker({"005930": Fill(3, 70000.0, "filled")})
    order_ids = execute_entries(
        conn, planned, broker=fb, cycle_id="CY1", order_mode="paper",
        market_map={"005930": "KOSPI"},
    )
    assert order_ids == ["CY1-005930-buy-0", "CY1-005930-stop-0"]   # 진입 + 손절 스톱
    t = conn.execute(
        'SELECT * FROM "Orders" WHERE "ClientOrderId"=\'CY1-005930-buy-0\''
    ).fetchone()
    assert t["Status"] == "filled" and t["FilledQuantity"] == 3
    assert t["OrderType"] == "00"                      # paper = IOC 미지원 보정
    assert t["Purpose"] == "entry" and t["Mode"] == "paper"
    p = conn.execute(
        'SELECT "PositionId", "Quantity", "AveragePrice", "CurrentStopPrice", "Market", '
        '"InitialStopPrice", "RiskPerShare", "ActiveStopOrderId" '
        'FROM "Positions" WHERE "SymbolId"=\'005930\''
    ).fetchone()
    assert p["Quantity"] == 3 and p["AveragePrice"] == 70000.0
    assert p["CurrentStopPrice"] == 65000.0
    assert p["Market"] == "KOSPI" and p["InitialStopPrice"] == 65000.0   # 시장 매핑·R고정
    assert p["RiskPerShare"] == 5000.0                                   # R = 70000 − 65000
    assert p["ActiveStopOrderId"] == "CY1-005930-stop-0"                 # 상주 스톱 연결
    # 손절 스톱(22)이 체결 수량만큼 등록됨(맨몸 포지션 방지 10-ops 10.3)
    s = conn.execute(
        'SELECT * FROM "Orders" WHERE "ClientOrderId"=\'CY1-005930-stop-0\''
    ).fetchone()
    assert s["Side"] == "sell" and s["OrderType"] == "22" and s["OrderQuantity"] == 3
    assert s["Purpose"] == "stop"
    assert s["TriggerPrice"] == 65000.0 and s["FilledQuantity"] == 0
    assert s["Status"] == "submitted"
    assert fb.stops[0]["trigger"] == 65000 and fb.stops[0]["qty"] == 3


def test_real_mode_uses_ioc(conn):
    _setup(conn)
    fb = FakeBroker()
    execute_entries(
        conn, [PlannedOrder("A", 1, 100.0, 90.0)],
        broker=fb, cycle_id="CY1", order_mode="real",
    )
    assert fb.calls[0]["ord_dvsn"] == ENTRY_ORD_DVSN["real"] == "11"


def test_no_fill_no_position(conn):
    _setup(conn)
    fb = FakeBroker({"A": Fill(0, None, "rejected")})
    execute_entries(conn, [PlannedOrder("A", 2, 100.0, 90.0)], broker=fb, cycle_id="CY1")
    t = conn.execute(
        'SELECT "Status", "FilledQuantity", "FilledDateTime" FROM "Orders"'
    ).fetchone()
    assert t["Status"] == "rejected" and t["FilledQuantity"] == 0
    assert t["FilledDateTime"] is None
    assert _count(conn, '"Positions"') == 0
    assert _count(conn, '"Orders"') == 1                # 미체결 → 스톱 없음
    assert fb.stops == []


def test_partial_fill(conn):
    _setup(conn)
    fb = FakeBroker({"A": Fill(1, 100.0, "partial")})
    execute_entries(conn, [PlannedOrder("A", 3, 100.0, 90.0)], broker=fb, cycle_id="CY1")
    t = conn.execute(
        'SELECT "Status", "OrderQuantity", "FilledQuantity" FROM "Orders" '
        'WHERE "Side"=\'buy\''
    ).fetchone()
    assert t["Status"] == "partial" and t["OrderQuantity"] == 3 and t["FilledQuantity"] == 1
    assert conn.execute(
        'SELECT "Quantity" FROM "Positions" WHERE "SymbolId"=\'A\''
    ).fetchone()["Quantity"] == 1
    # 스톱은 체결 수량(1)만큼만 등록
    assert conn.execute(
        'SELECT "OrderQuantity" FROM "Orders" WHERE "Purpose"=\'stop\''
    ).fetchone()["OrderQuantity"] == 1


def test_fill_without_price_falls_back_to_order_price(conn):
    """체결됐는데 체결가 미파싱(avg_prvs 0/누락)이면 주문가로 폴백 — 맨몸 포지션 방지.

    KIS가 filled_qty>0은 주면서 평단을 0/누락으로 줄 때, 포지션·손절 스톱이 통째로
    누락되면 추적 안 되는 맨몸 포지션이 된다. Orders엔 원값(None)을 남기되 Positions는
    주문가로 채워 장부·스톱을 보존한다.
    """
    _setup(conn)
    fb = FakeBroker({"A": Fill(2, None, "filled")})       # 체결 2주, 체결가 없음
    execute_entries(conn, [PlannedOrder("A", 2, 100.0, 90.0)], broker=fb, cycle_id="CY1")
    p = conn.execute(
        'SELECT "Quantity", "AveragePrice", "CurrentStopPrice" FROM "Positions" '
        'WHERE "SymbolId"=\'A\''
    ).fetchone()
    assert p is not None and p["Quantity"] == 2           # 포지션이 누락되지 않음
    assert p["AveragePrice"] == 100.0                     # 주문가로 폴백
    assert p["CurrentStopPrice"] == 90.0
    # Orders엔 broker 원값(None) 보존
    t = conn.execute(
        'SELECT "AverageFillPrice", "FilledQuantity" FROM "Orders" WHERE "Side"=\'buy\''
    ).fetchone()
    assert t["AverageFillPrice"] is None and t["FilledQuantity"] == 2
    # 손절 스톱(22)도 체결 수량만큼 등록됨
    assert conn.execute(
        'SELECT "OrderQuantity" FROM "Orders" WHERE "Purpose"=\'stop\''
    ).fetchone()["OrderQuantity"] == 2
    assert fb.stops and fb.stops[0]["qty"] == 2


def test_add_position_weighted_avg(conn):
    _setup(conn)
    execute_entries(
        conn, [PlannedOrder("A", 2, 100.0, 90.0)],
        broker=FakeBroker({"A": Fill(2, 100.0, "filled")}), cycle_id="CY1",
    )
    journal.create_cycle(conn, "CY2")
    execute_entries(
        conn, [PlannedOrder("A", 2, 200.0, 180.0)],
        broker=FakeBroker({"A": Fill(2, 200.0, "filled")}), cycle_id="CY2",
    )
    p = conn.execute(
        'SELECT "Quantity", "AveragePrice" FROM "Positions" WHERE "SymbolId"=\'A\''
    ).fetchone()
    assert p["Quantity"] == 4 and p["AveragePrice"] == 150.0   # (2·100+2·200)/4
    assert _count(conn, '"Positions"') == 1
    assert _count(conn, '"Orders"', 'WHERE "Side"=\'buy\'') == 2
    assert _count(conn, '"Orders"', 'WHERE "Purpose"=\'stop\'') == 2
