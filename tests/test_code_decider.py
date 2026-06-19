"""B안 코드 결정 — 종합점수·진입/청산 임계 (agents/code_decider, 10-3.1 B)."""
import pytest

from agents.code_decider import Candidate, composite_score, decide
from config.settings import load_params
from core.schemas import OrderAction


@pytest.fixture
def params():
    return load_params("risk_params")


def test_composite_with_catalyst(params):
    d = params["decision"]                       # w_screen 0.6, w_catalyst 0.4
    # (0.6*0.8 + 0.4*0.5) / 1.0 = 0.68
    assert composite_score(0.8, 0.5, d) == pytest.approx(0.68)


def test_composite_missing_catalyst_renormalizes(params):
    # 촉매 결측 → 스크리너만(재정규화)
    assert composite_score(0.8, None, params["decision"]) == pytest.approx(0.8)


def test_new_buy_above_threshold(params):
    cands = [Candidate("005930", screen_score=0.9, catalyst_score=0.9)]
    out = decide(cands, set(), params)
    assert len(out.orders) == 1
    assert out.orders[0].action is OrderAction.BUY


def test_new_no_trade_below_threshold(params):
    cands = [Candidate("A", screen_score=0.3, catalyst_score=0.3)]
    out = decide(cands, set(), params)
    assert out.orders == []                       # τ=0.6 미만 → 무거래


def test_held_invalidation_sells(params):
    # 보유인데 점수가 exit_threshold(0.4) 아래 → 청산 제안
    cands = [Candidate("A", screen_score=0.2, catalyst_score=0.2)]
    out = decide(cands, {"A"}, params)
    assert out.orders[0].action is OrderAction.SELL


def test_held_strong_holds(params):
    cands = [Candidate("A", screen_score=0.8, catalyst_score=0.8)]
    out = decide(cands, {"A"}, params)
    assert out.orders[0].action is OrderAction.HOLD


def test_notes_label(params):
    out = decide([], set(), params)
    assert "B안" in out.notes
