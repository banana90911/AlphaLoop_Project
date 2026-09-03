"""
description:        청산 규칙 — 우선순위·R 고정 (exec/exits, 05-risk §129).
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date

import pandas as pd

from exec.exits import (
    Position,
    StopGapHit,
    StopPosition,
    _days_held,
    decide_exit,
    detect_stop_gaps,
    execute_exits,
)
from exec.orders import Fill, execute_entries
from memory import journal
from pipeline.cycle import PlannedOrder

_P = {"exits": {"breakeven_R": 1.5, "trail_k": 2.75,
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


def test_breakeven_raises_stop_no_partial_sell():
    # R=1000, +1.5R=11,500 도달 → 부분 매도 없이 손절만 본전(10,000)으로
    a = decide_exit(_pos(), price=11_500, atr=200, params=_P)
    assert a.action == "raise_stop" and a.reason == "breakeven"
    assert a.new_stop == 10_000


def test_breakeven_skipped_if_done():
    # 이미 본전 상향 완료 → 트레일링으로 넘어간다
    a = decide_exit(_pos(breakeven_done=True, current_stop=10_000),
                    price=11_500, atr=200, params=_P)
    assert a.action == "raise_stop" and a.reason == "trail"


def test_breakeven_never_lowers_stop():
    # 트레일링이 이미 본전 위(10,500)면 본전 상향이 손절을 내리지 않는다
    a = decide_exit(_pos(current_stop=10_500), price=11_500, atr=200, params=_P)
    assert a.new_stop == 10_500


def test_trailing_raises_stop():
    # price 12,000, trail 2.75*200=550 → new_stop 11,450 > current 9,000
    a = decide_exit(_pos(breakeven_done=True), price=12_000, atr=200, params=_P)
    assert a.action == "raise_stop"
    assert a.new_stop == 12_000 - 2.75 * 200


def test_time_exit_when_stale():
    # 21일 보유, 제자리(+0.5R=10,500 미만), 트레일링 갱신 없음
    a = decide_exit(_pos(days_held=21, current_stop=9_500), price=10_200, atr=300, params=_P)
    # 트레일링 new_stop=10,200-825=9,375 < current 9,500 → 갱신 없음 → 시간청산
    assert a.action == "exit_full" and a.reason == "time_exit"


def test_no_time_exit_if_trending():
    # 보유 21일이어도 +0.5R 넘었으면 시간청산 면제(여기선 트레일링이 잡음)
    a = decide_exit(_pos(days_held=21, breakeven_done=True), price=11_000, atr=100, params=_P)
    assert a.action != "exit_full"


def test_hold():
    a = decide_exit(_pos(breakeven_done=True, current_stop=9_900),
                    price=9_950, atr=100, params=_P)
    # 손절 위, 본전 상향 완료, 트레일링 new=9,950-275=9,675<9,900, 보유 1일 → hold
    assert a.action == "hold"


# ── 손절 구멍 트리거 감지 (detect_stop_gaps — 이벤트 사이클 트리거 판정) ──
def test_stop_gap_detected_below_stop():
    # 갭하락으로 현재가가 손절가 아래인데 아직 보유 → 손절 구멍
    hits = detect_stop_gaps([StopPosition("005930", 65000.0, 3)], {"005930": 60000.0})
    assert hits == [StopGapHit("005930", 60000.0, 65000.0)]


def test_stop_gap_at_stop_is_hit():
    # 경계: 현재가 == 손절가도 이탈로 본다(price ≤ stop, decide_exit ②와 동일 임계)
    hits = detect_stop_gaps([StopPosition("005930", 65000.0, 3)], {"005930": 65000.0})
    assert len(hits) == 1


def test_stop_gap_none_above_stop():
    # 손절가 위(정상) → 트리거 없음
    assert detect_stop_gaps([StopPosition("005930", 65000.0, 3)], {"005930": 70000.0}) == []


def test_stop_gap_skips_flat_position():
    # 이미 청산돼 잔고 0(자동 체결된 스톱 반영) → 손절 구멍 아님
    assert detect_stop_gaps([StopPosition("005930", 65000.0, 0)], {"005930": 60000.0}) == []


def test_stop_gap_skips_missing_or_bad_price():
    # 현재가 결측·비정상은 건너뜀(다음 폴링 재시도) — 없는 값으로 오탐하지 않는다
    pos = [StopPosition("005930", 65000.0, 3), StopPosition("000660", 50000.0, 2)]
    assert detect_stop_gaps(pos, {"000660": 0.0}) == []          # 결측·0 모두 스킵


def test_stop_gap_multi_position_filters():
    pos = [
        StopPosition("A", 100.0, 1),    # 90 ≤ 100 → hit
        StopPosition("B", 100.0, 1),    # 110 > 100 → no
        StopPosition("C", 100.0, 1),    # 결측 → skip
    ]
    hits = detect_stop_gaps(pos, {"A": 90.0, "B": 110.0})
    assert [h.symbol for h in hits] == ["A"]


# ── execute_exits 집행 통합 (FakeBroker로 송출→Orders·Outcomes·Positions) ──
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


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


def _enter(conn, broker, cycle="CY1", price=70000.0, stop=65000.0, qty=3):
    journal.create_cycle(conn, cycle)
    execute_entries(
        conn, [PlannedOrder("005930", qty, price, stop)],
        broker=broker, cycle_id=cycle, order_mode="paper",
    )


def test_execute_stop_hit_full_exit(conn):
    fb = _FakeBroker()
    _enter(conn, fb)
    journal.create_cycle(conn, "CY2")
    order_ids = execute_exits(conn, {"005930": _df(60000.0)}, broker=fb,
                              cycle_id="CY2", order_mode="paper")  # 60000 < 손절 65000
    assert order_ids == ["CY2-005930-exit-0"]
    o = conn.execute(
        'SELECT * FROM "Outcomes" WHERE "SymbolId"=\'005930\''
    ).fetchone()
    assert o["Quantity"] == 3 and o["ExitReason"] == "stopHit"   # 07-model CHECK 값
    assert o["NetProfitLoss"] < 0 and o["ExitKind"] == "full"
    assert o["RMultiple"] < 0                                    # 손절이니 −1R 근처
    pos = conn.execute('SELECT "Status", "Quantity" FROM "Positions"').fetchone()
    assert pos["Status"] == "closed" and pos["Quantity"] == 0
    t = conn.execute(
        'SELECT "Side", "OrderType", "Purpose" FROM "Orders" '
        'WHERE "ClientOrderId"=\'CY2-005930-exit-0\''
    ).fetchone()
    assert t["Side"] == "sell" and t["OrderType"] == "01"   # paper 시장가 보정
    assert t["Purpose"] == "exit"


def test_execute_forced_sell_invalidation(conn):
    fb = _FakeBroker()
    _enter(conn, fb)
    journal.create_cycle(conn, "CY2")
    execute_exits(conn, {"005930": _df(72000.0)}, broker=fb, cycle_id="CY2",
                  forced_sells=["005930"], order_mode="paper")   # 손절 위지만 결정이 sell
    assert conn.execute(
        'SELECT "ExitReason" FROM "Outcomes"'
    ).fetchone()["ExitReason"] == "thesisInvalid"
    assert conn.execute(
        'SELECT "Status" FROM "Positions"'
    ).fetchone()["Status"] == "closed"


def test_execute_breakeven_raises_stop_only(conn):
    """+1.5R 도달 — 매도 없이 손절만 본전으로. 부분 익절은 설계에 없다."""
    fb = _FakeBroker()
    _enter(conn, fb)                                   # R=5000, +1.5R=77500
    journal.create_cycle(conn, "CY2")
    execute_exits(conn, {"005930": _df(78000.0)}, broker=fb, cycle_id="CY2", order_mode="paper")
    pos = conn.execute(
        'SELECT "Quantity", "CurrentStopPrice", "IsBreakevenDone" FROM "Positions"'
    ).fetchone()
    assert pos["Quantity"] == 3                         # 수량 그대로(매도 없음)
    assert pos["CurrentStopPrice"] == 70000.0           # 본전으로 상향
    assert pos["IsBreakevenDone"] is True               # 다음 사이클에 ③이 또 걸리지 않게
    assert _count(conn, '"Outcomes"') == 0


def test_execute_hold_no_action(conn):
    fb = _FakeBroker()
    _enter(conn, fb)
    journal.create_cycle(conn, "CY2")
    order_ids = execute_exits(conn, {"005930": _df(70000.0)}, broker=fb,
                              cycle_id="CY2", order_mode="paper")
    assert order_ids == [] and fb.exits == []
    assert _count(conn, '"Outcomes"') == 0
    assert conn.execute(
        'SELECT "Status" FROM "Positions"'
    ).fetchone()["Status"] == "open"


# ── 보유일수는 거래일로 센다 (06-sizing 6.2) ──

def test_days_held_counts_trading_days():
    # 2026-08-03(월) → 2026-08-10(월): 달력 7일, 거래일 5일
    assert _days_held(date(2026, 8, 3), date(2026, 8, 10)) == 5


def test_days_held_skips_weekend():
    assert _days_held(date(2026, 8, 7), date(2026, 8, 9)) == 0    # 금 → 일: 거래일 0


def test_days_held_is_less_than_calendar_days():
    start, end = date(2026, 8, 3), date(2026, 8, 31)
    assert _days_held(start, end) < (end - start).days        # 달력일보다 항상 적다


def test_time_exit_needs_more_calendar_days_now():
    """20거래일 기준이면 달력으로는 4주가 넘어야 시간청산에 닿는다."""
    start = date(2026, 8, 3)
    assert _days_held(start, date(2026, 8, 24)) <= 20           # 달력 21일: 아직 미달
    assert _days_held(start, date(2026, 9, 2)) > 20             # 달력 30일: 초과


def test_days_held_handles_missing_entry_date():
    assert _days_held(None, None) == 0
