"""청산 규칙 — 우선순위·R 고정 (exec/exits, 05-risk §129)."""
import pandas as pd

from exec.exits import Position, decide_exit, execute_exits
from exec.orders import Fill, execute_entries
from memory import journal
from memory.db import init_db
from pipeline.trading_cycle import PlannedOrder

_P = {"exits": {"tp1_R": 1.5, "tp1_frac": 0.4, "trail_k": 2.75,
                "max_hold_days": 20, "min_progress_R": 0.5}}


def _pos(**kw):
    base = dict(entry_price=10_000, initial_stop=9_000, current_stop=9_000, days_held=1)
    base.update(kw)
    return Position(**base)


def test_thesis_invalid_first_priority():
    # 손절도 깨졌지만 논지무효가 우선
    a = decide_exit(_pos(thesis_valid=False), price=8_000, atr=200, params=_P)
    assert a.action == "exit_full" and a.reason == "thesis_invalid"


def test_invalidation_price_triggers():
    a = decide_exit(_pos(invalidation_price=9_500), price=9_400, atr=200, params=_P)
    assert a.action == "exit_full" and a.reason == "thesis_invalid"


def test_stop_hit():
    a = decide_exit(_pos(), price=8_900, atr=200, params=_P)
    assert a.action == "exit_full" and a.reason == "stop_hit"


def test_tp1_partial_and_breakeven():
    # R=1000, +1.5R=11,500 도달 → 부분익절 + 손절 본전(10,000)
    a = decide_exit(_pos(), price=11_500, atr=200, params=_P)
    assert a.action == "exit_partial"
    assert a.fraction == 0.4
    assert a.new_stop == 10_000


def test_tp1_skipped_if_done():
    # 이미 tp1 완료 → tp1 건너뛰고 트레일링으로
    a = decide_exit(_pos(tp1_done=True, current_stop=10_000), price=11_500, atr=200, params=_P)
    assert a.action == "raise_stop"


def test_trailing_raises_stop():
    # price 12,000, trail 2.75*200=550 → new_stop 11,450 > current 9,000
    a = decide_exit(_pos(tp1_done=True), price=12_000, atr=200, params=_P)
    assert a.action == "raise_stop"
    assert a.new_stop == 12_000 - 2.75 * 200


def test_time_exit_when_stale():
    # 21일 보유, 제자리(+0.5R=10,500 미만), 트레일링 갱신 없음
    a = decide_exit(_pos(days_held=21, current_stop=9_500), price=10_200, atr=300, params=_P)
    # 트레일링 new_stop=10,200-825=9,375 < current 9,500 → 갱신 없음 → 시간청산
    assert a.action == "exit_full" and a.reason == "time_exit"


def test_no_time_exit_if_trending():
    # 보유 21일이어도 +0.5R 넘었으면 시간청산 면제(여기선 트레일링이 잡음)
    a = decide_exit(_pos(days_held=21, tp1_done=True), price=11_000, atr=100, params=_P)
    assert a.action != "exit_full"


def test_hold():
    a = decide_exit(_pos(tp1_done=True, current_stop=9_900), price=9_950, atr=100, params=_P)
    # 손절 위, tp1 완료, 트레일링 new=9,950-275=9,675<9,900, 보유 1일 → hold
    assert a.action == "hold"


# ── execute_exits 집행 통합 (FakeBroker로 송출→trades·outcomes·positions) ──
class _FakeBroker:
    def __init__(self, exit_fills=None):
        self.exit_fills = exit_fills or {}
        self.exits: list[tuple] = []

    def place_entry(self, *, code, qty, price, ord_dvsn, client_order_id) -> Fill:
        return Fill(qty, float(price), "filled")

    def place_stop(self, *, code, qty, trigger_price, limit_price, client_order_id) -> Fill:
        return Fill(0, None, "submitted", "S")

    def place_exit(self, *, code, qty, ord_dvsn, client_order_id) -> Fill:
        self.exits.append((code, qty, ord_dvsn))
        return self.exit_fills.get(code, Fill(qty, None, "filled"))   # None → 현재가 사용


def _df(last_close: float, base: float = 70000.0, n: int = 300) -> pd.DataFrame:
    closes = [base] * (n - 1) + [last_close]
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"open": closes, "high": [c * 1.005 for c in closes],
         "low": [c * 0.995 for c in closes], "close": closes,
         "volume": [1_000_000.0] * n},
        index=idx,
    )


def _enter(conn, broker, cycle="CY1", price=70000.0, stop=65000.0, qty=3):
    journal.create_cycle(conn, cycle, "scheduled")
    execute_entries(
        conn, [PlannedOrder("005930", qty, price, stop)],
        broker=broker, cycle_id=cycle, order_mode="paper",
    )


def test_execute_stop_hit_full_exit(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    fb = _FakeBroker()
    _enter(conn, fb)
    journal.create_cycle(conn, "CY2", "scheduled")
    tids = execute_exits(conn, {"005930": _df(60000.0)}, broker=fb,
                         cycle_id="CY2", order_mode="paper")   # 60000 < 손절 65000
    assert tids == ["CY2-005930-exit-0"]
    o = conn.execute("SELECT * FROM outcomes WHERE symbol='005930'").fetchone()
    assert o["qty"] == 3 and o["exit_reason"] == "stop_hit" and o["net_pnl"] < 0
    pos = conn.execute("SELECT status, qty FROM positions").fetchone()
    assert pos["status"] == "closed" and pos["qty"] == 0
    t = conn.execute(
        "SELECT side, ord_dvsn FROM trades WHERE trade_id='CY2-005930-exit-0'"
    ).fetchone()
    assert t["side"] == "sell" and t["ord_dvsn"] == "01"   # paper 시장가 보정
    conn.close()


def test_execute_llm_sell_invalidation(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    fb = _FakeBroker()
    _enter(conn, fb)
    journal.create_cycle(conn, "CY2", "scheduled")
    execute_exits(conn, {"005930": _df(72000.0)}, broker=fb, cycle_id="CY2",
                  llm_sells=["005930"], order_mode="paper")   # 손절 위지만 LLM sell
    assert conn.execute("SELECT exit_reason FROM outcomes").fetchone()["exit_reason"] == "thesis_invalid"
    assert conn.execute("SELECT status FROM positions").fetchone()["status"] == "closed"
    conn.close()


def test_execute_tp1_partial(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    fb = _FakeBroker()
    _enter(conn, fb)                                   # R=5000, +1.5R=77500
    journal.create_cycle(conn, "CY2", "scheduled")
    execute_exits(conn, {"005930": _df(78000.0)}, broker=fb, cycle_id="CY2", order_mode="paper")
    pos = conn.execute("SELECT qty, tp1_done, current_stop_price FROM positions").fetchone()
    assert pos["qty"] < 3 and pos["tp1_done"] == 1 and pos["current_stop_price"] == 70000.0
    o = conn.execute("SELECT exit_reason, net_pnl FROM outcomes").fetchone()
    assert o["exit_reason"] == "tp1" and o["net_pnl"] > 0
    conn.close()


def test_execute_hold_no_action(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    fb = _FakeBroker()
    _enter(conn, fb)
    journal.create_cycle(conn, "CY2", "scheduled")
    tids = execute_exits(conn, {"005930": _df(70000.0)}, broker=fb,
                         cycle_id="CY2", order_mode="paper")
    assert tids == [] and fb.exits == []
    assert conn.execute("SELECT COUNT(*) c FROM outcomes").fetchone()["c"] == 0
    assert conn.execute("SELECT status FROM positions").fetchone()["status"] == "open"
    conn.close()
