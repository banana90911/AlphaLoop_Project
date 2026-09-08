"""
description:        장중 보유 감시 — 브로커가 스스로 체결한 손절의 장부 반영
author:             siheon jung
created date:       2026/09/09
remarks:            걸어 둔 손절은 우리가 부르지 않아도 체결된다. 그걸 못 잡으면
                    다음 사이클의 잔고 대조가 정상 손절을 '보유 불일치'로 보고
                    매매 전체를 멈춘다(05-risk 5.2).
"""

import pytest

from exec.orders import Fill, execute_entries
from memory import journal
from pipeline.cycle import PlannedOrder
from run_watch import (
    find_filled_stops,
    find_missing_stops,
    load_open_positions,
    settle_filled_stops,
)

_STOP = "22"


class _FakeBroker:
    """진입 1건과 손절 예약 1건만 받아주는 최소 브로커."""

    def place_entry(self, *, code, qty, price, ord_dvsn, client_order_id) -> Fill:
        return Fill(qty, float(price), "filled", "ODNO-ENTRY", broker_org_no="06010")

    def place_stop(self, *, code, qty, trigger_price, limit_price, client_order_id) -> Fill:
        return Fill(0, None, "submitted", "ODNO-STOP", broker_org_no="06010")


@pytest.fixture
def held(conn):
    """005930 3주 @70,000, 손절 예약 65,000이 걸린 상태."""
    journal.create_cycle(conn, "CY1")
    execute_entries(
        conn, [PlannedOrder("005930", 3, 70_000.0, 65_000.0)],
        broker=_FakeBroker(), cycle_id="CY1", order_mode="paper",
    )
    return load_open_positions(conn)


def _kis_order(*, filled: int, qty: int = 3, price: float | None = 65_000.0,
               odno: str = "ODNO-STOP", cancelled: str = "N") -> dict:
    return {
        "odno": odno, "pdno": "005930", "ord_dvsn_cd": _STOP,
        "ord_qty": str(qty), "tot_ccld_qty": str(filled),
        "avg_prvs": str(price) if price else "0", "cncl_yn": cancelled,
    }


# ── 감지 ────────────────────────────────────────────────────────
def test_체결된_손절을_찾아낸다(held):
    hits = find_filled_stops([_kis_order(filled=3)], held)
    assert len(hits) == 1
    assert hits[0]["filled"] == 3 and hits[0]["price"] == 65_000.0


def test_미체결_예약은_체결로_보지_않는다(held):
    assert find_filled_stops([_kis_order(filled=0)], held) == []


def test_주문번호가_다르면_남의_주문이다(held):
    """같은 종목이라도 우리가 건 예약이 아니면 장부를 건드리지 않는다."""
    assert find_filled_stops([_kis_order(filled=3, odno="ODNO-OTHER")], held) == []


def test_체결_수량은_보유_수량을_넘지_않는다(held):
    """KIS가 우리 보유보다 큰 수량을 돌려줘도 없는 주식을 팔았다고 적지 않는다."""
    hits = find_filled_stops([_kis_order(filled=99)], held)
    assert hits[0]["filled"] == 3


# ── 정산 ────────────────────────────────────────────────────────
def test_체결된_손절이_장부에_반영된다(conn, held):
    settled = settle_filled_stops(conn, find_filled_stops([_kis_order(filled=3)], held),
                                  mode="paper")
    assert len(settled) == 1 and settled[0]["code"] == "005930"
    assert settled[0]["net"] < 0                       # 70,000 → 65,000

    pos = conn.execute('SELECT status, quantity FROM positions').fetchone()
    assert pos["status"] == "closed" and pos["quantity"] == 0

    out = conn.execute('SELECT * FROM outcomes').fetchone()
    assert out["exit_reason"] == "stopHit" and out["exit_kind"] == "full"
    assert out["quantity"] == 3 and float(out["exit_price"]) == 65_000.0

    o = conn.execute(
        "SELECT status, filled_quantity FROM orders WHERE purpose='stop'"
    ).fetchone()
    assert o["status"] == "filled" and o["filled_quantity"] == 3


def test_부분_체결은_보유를_줄이고_열어_둔다(conn, held):
    settle_filled_stops(conn, find_filled_stops([_kis_order(filled=1)], held),
                        mode="paper")
    pos = conn.execute('SELECT status, quantity FROM positions').fetchone()
    assert pos["status"] == "open" and pos["quantity"] == 2
    assert conn.execute('SELECT exit_kind FROM outcomes').fetchone()["exit_kind"] == "partial"


def test_체결가를_못_받으면_발동가로_대신한다(conn, held):
    """손익을 0으로 남기는 것보다 발동가로 적는 편이 실제에 가깝다."""
    settle_filled_stops(conn, find_filled_stops([_kis_order(filled=3, price=None)], held),
                        mode="paper")
    assert float(conn.execute('SELECT exit_price FROM outcomes').fetchone()["exit_price"]) \
        == 65_000.0


def test_이미_닫힌_보유는_두_번_적재하지_않는다(conn, held):
    """사이클이 먼저 청산했을 수 있다 — 그때 또 적으면 손익이 두 배가 된다."""
    hits = find_filled_stops([_kis_order(filled=3)], held)
    settle_filled_stops(conn, hits, mode="paper")
    assert settle_filled_stops(conn, hits, mode="paper") == []
    assert conn.execute('SELECT COUNT(*) AS c FROM outcomes').fetchone()["c"] == 1


# ── 손절이 체결된 종목에 손절을 다시 걸지 않는다 ────────────────
def test_체결된_보유를_빼고_나면_재등록_대상이_없다(conn, held):
    """체결된 예약은 KIS에서 '살아 있지 않다'로 보여, 그대로 두면 재등록 대상이 된다."""
    orders = [_kis_order(filled=3)]
    assert find_missing_stops(orders, held)            # 거르지 않으면 재등록 후보

    settle_filled_stops(conn, find_filled_stops(orders, held), mode="paper")
    assert load_open_positions(conn) == []             # 보유가 없으니 대상도 없다
