"""
description:        진입 지정가 = 매도1호가 + IOC 미체결 취소 판정 (10-ops 10.13)
author:             siheon jung
created date:       2026/09/16
remarks:            2026-09-16 첫 실거래: 직전 체결가로 낸 IOC 매수 4건이 전부 0주 체결로
                    자동취소됐고, 장부에는 `submitted`로 남아 대시보드 안전 출금 가능액이
                    28,580원 줄어 보였다. 아래 원장 행은 그날 KIS가 실제로 돌려준 모양이다.
"""

from broker.kis_client import _entry_status
from core import ticks
from pipeline import cycle, gates
from pipeline.gates import Quote
from risk.risk_engine import Account, StockStatus
from tests.test_cycle import _entry_params, _universe

# 2026-09-16 14:35 한화생명 진입 — IOC 매수 자동취소
_IOC_CANCELLED = {"odno": "0014207600", "pdno": "088350", "ord_dvsn_cd": "11",
                  "ord_qty": "1", "ord_unpr": "5800", "tot_ccld_qty": "0",
                  "avg_prvs": "0", "cncl_yn": "", "rmn_qty": "0",
                  "sll_buy_dvsn_cd_name": "IOC매수자동취소*"}


# ── 원장 → 주문 상태 ────────────────────────────────────────────────────────

def test_체결0주에_잔량0이면_취소다():
    assert _entry_status(_IOC_CANCELLED, 0, 1) == "cancelled"


def test_잔량이_남아_있으면_아직_취소로_단정하지_않는다():
    """조회가 체결을 반영하기 전 순간일 수 있다."""
    assert _entry_status({**_IOC_CANCELLED, "rmn_qty": "1"}, 0, 1) == "submitted"


def test_잔량_칸이_없으면_취소로_단정하지_않는다():
    row = {k: v for k, v in _IOC_CANCELLED.items() if k != "rmn_qty"}
    assert _entry_status(row, 0, 1) == "submitted"


def test_체결되면_잔량과_무관하게_체결이다():
    assert _entry_status({**_IOC_CANCELLED, "tot_ccld_qty": "1"}, 1, 1) == "filled"
    assert _entry_status(_IOC_CANCELLED, 1, 3) == "partial"


# ── 호가 응답 파싱 ──────────────────────────────────────────────────────────

def test_매도1호가를_꺼낸다():
    """필드명은 2026-09-16 서버에서 실측 — output1.askp1."""
    assert gates.ask_of({"output1": {"askp1": "5870", "bidp1": "5860"}}) == 5870.0


def test_매도_물량이_없으면_None():
    """상한가면 askp1이 0으로 온다 — 0원 주문을 내면 안 된다."""
    assert gates.ask_of({"output1": {"askp1": "0"}}) is None
    assert gates.ask_of({}) is None


class _Client:
    """현재가는 주고, 호가는 종목에 따라 실패하는 가짜 KIS."""

    def __init__(self, ask_fail: set[str]) -> None:
        self.ask_fail = ask_fail

    def get_price(self, code):
        return {"output": {"stck_prpr": "5860", "stck_mxpr": "99999", "stck_llam": "1"}}

    def get_asking_price(self, code):
        if code in self.ask_fail:
            raise RuntimeError("초당 거래건수 초과")
        return {"output1": {"askp1": "5870"}}


def test_시세에_매도1호가가_실린다():
    q = gates.fetch_quotes(_Client(set()), ["088350"])["088350"]
    assert q.last_price == 5860.0 and q.ask_price == 5870.0


def test_호가_조회가_실패해도_종목을_빼지_않는다():
    """상태는 이미 안다. 종목을 빼면 '모름=진입 불가'로 멀쩡한 후보가 사라진다."""
    got = gates.fetch_quotes(_Client({"088350"}), ["088350"])
    assert "088350" in got and got["088350"].ask_price is None


# ── 사이클이 매도1호가로 진입가·손절가를 잡는가 ──────────────────────────────

def _plan(conn, **quotes):
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc,
                    params=_entry_params(), quotes=quotes)
    return {o.code: o for o in res.planned_orders}, res


def test_진입가는_매도1호가다(conn):
    planned, _ = _plan(conn, UP1=Quote(7500.0, StockStatus(), ask_price=7510.0),
                       UP2=Quote(4200.0, StockStatus(), ask_price=4205.0))
    assert planned["UP1"].price == 7510.0
    assert planned["UP2"].price == 4205.0


def test_손절가도_매도1호가_기준에_호가단위로_맞춘다(conn):
    planned, res = _plan(conn, UP1=Quote(7500.0, StockStatus(), ask_price=7510.0),
                         UP2=Quote(4200.0, StockStatus(), ask_price=4205.0))
    atr = conn.execute(
        "SELECT atr FROM cycle_scores WHERE cycle_id=%s AND symbol_id='UP1'",
        (res.cycle_id,),
    ).fetchone()["atr"]
    assert planned["UP1"].stop == float(ticks.align_down(7510.0 - 2.0 * atr))
    assert ticks.is_aligned(planned["UP1"].stop)


def test_호가가_없으면_현재가로_대신한다(conn):
    planned, _ = _plan(conn, UP1=Quote(7500.0, StockStatus()),
                       UP2=Quote(4200.0, StockStatus()))
    assert planned["UP1"].price == 7500.0
