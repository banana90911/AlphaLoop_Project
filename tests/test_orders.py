"""exec/orders.py — 신규 진입 송출·체결 적재(7단계). FakeBroker로 흐름 검증."""
from __future__ import annotations

from exec.orders import ENTRY_ORD_DVSN, Fill, execute_entries
from memory import journal
from memory.db import init_db
from pipeline.trading_cycle import PlannedOrder


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


def _setup(tmp_path, cycle_id="CY1"):
    conn = init_db(str(tmp_path / "t.db"))
    journal.create_cycle(conn, cycle_id, "scheduled")
    return conn


def test_entry_fill_records_trade_and_position(tmp_path):
    conn = _setup(tmp_path)
    planned = [PlannedOrder("005930", 3, 70000.0, 65000.0)]
    fb = FakeBroker({"005930": Fill(3, 70000.0, "filled")})
    tids = execute_entries(
        conn, planned, broker=fb, cycle_id="CY1", order_mode="paper"
    )
    assert tids == ["CY1-005930-buy-0", "CY1-005930-stop-0"]   # 진입 + 손절 스톱
    t = conn.execute("SELECT * FROM trades WHERE trade_id='CY1-005930-buy-0'").fetchone()
    assert t["status"] == "filled" and t["filled_qty"] == 3
    assert t["ord_dvsn"] == "00"                       # paper = IOC 미지원 보정
    assert t["client_order_id"] == "CY1-005930-buy-0"
    p = conn.execute(
        "SELECT qty, avg_price, current_stop_price FROM positions WHERE symbol='005930'"
    ).fetchone()
    assert p["qty"] == 3 and p["avg_price"] == 70000.0 and p["current_stop_price"] == 65000.0
    # 손절 스톱(22)이 체결 수량만큼 등록됨(맨몸 포지션 방지 11-2.3)
    s = conn.execute("SELECT * FROM trades WHERE trade_id='CY1-005930-stop-0'").fetchone()
    assert s["side"] == "sell" and s["ord_dvsn"] == "22" and s["order_qty"] == 3
    assert s["trigger_price"] == 65000.0 and s["filled_qty"] == 0 and s["status"] == "submitted"
    assert fb.stops[0]["trigger"] == 65000 and fb.stops[0]["qty"] == 3
    conn.close()


def test_real_mode_uses_ioc(tmp_path):
    conn = _setup(tmp_path)
    fb = FakeBroker()
    execute_entries(
        conn, [PlannedOrder("A", 1, 100.0, 90.0)],
        broker=fb, cycle_id="CY1", order_mode="real",
    )
    assert fb.calls[0]["ord_dvsn"] == ENTRY_ORD_DVSN["real"] == "11"
    conn.close()


def test_no_fill_no_position(tmp_path):
    conn = _setup(tmp_path)
    fb = FakeBroker({"A": Fill(0, None, "rejected")})
    execute_entries(conn, [PlannedOrder("A", 2, 100.0, 90.0)], broker=fb, cycle_id="CY1")
    t = conn.execute("SELECT status, filled_qty, filled_at FROM trades").fetchone()
    assert t["status"] == "rejected" and t["filled_qty"] == 0 and t["filled_at"] is None
    assert conn.execute("SELECT COUNT(*) c FROM positions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM trades").fetchone()["c"] == 1   # 미체결 → 스톱 없음
    assert fb.stops == []
    conn.close()


def test_partial_fill(tmp_path):
    conn = _setup(tmp_path)
    fb = FakeBroker({"A": Fill(1, 100.0, "partial")})
    execute_entries(conn, [PlannedOrder("A", 3, 100.0, 90.0)], broker=fb, cycle_id="CY1")
    t = conn.execute(
        "SELECT status, order_qty, filled_qty FROM trades WHERE side='buy'"
    ).fetchone()
    assert t["status"] == "partial" and t["order_qty"] == 3 and t["filled_qty"] == 1
    assert conn.execute("SELECT qty FROM positions WHERE symbol='A'").fetchone()["qty"] == 1
    # 스톱은 체결 수량(1)만큼만 등록
    assert conn.execute(
        "SELECT order_qty FROM trades WHERE ord_dvsn='22'"
    ).fetchone()["order_qty"] == 1
    conn.close()


def test_add_position_weighted_avg(tmp_path):
    conn = _setup(tmp_path)
    execute_entries(
        conn, [PlannedOrder("A", 2, 100.0, 90.0)],
        broker=FakeBroker({"A": Fill(2, 100.0, "filled")}), cycle_id="CY1",
    )
    journal.create_cycle(conn, "CY2", "scheduled")
    execute_entries(
        conn, [PlannedOrder("A", 2, 200.0, 180.0)],
        broker=FakeBroker({"A": Fill(2, 200.0, "filled")}), cycle_id="CY2",
    )
    p = conn.execute("SELECT qty, avg_price FROM positions WHERE symbol='A'").fetchone()
    assert p["qty"] == 4 and p["avg_price"] == 150.0     # (2·100+2·200)/4
    assert conn.execute("SELECT COUNT(*) c FROM positions").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM trades WHERE side='buy'").fetchone()["c"] == 2
    assert conn.execute("SELECT COUNT(*) c FROM trades WHERE ord_dvsn='22'").fetchone()["c"] == 2
    conn.close()
