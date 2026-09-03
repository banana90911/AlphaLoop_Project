"""
description:        결정 규칙 — 점수 임계로 신규 진입·보유 청산 (pipeline/decision).
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import pytest

from config.settings import load_params
from core.schemas import OrderAction
from pipeline.decision import Candidate, decide, run_decision


@pytest.fixture
def params():
    return load_params("risk_params")


def test_new_buy_above_threshold(params):
    out = decide([Candidate("005930", 0.9)], set(), params)
    assert len(out.orders) == 1
    assert out.orders[0].action is OrderAction.BUY
    assert out.orders[0].risk_budget == pytest.approx(0.9)


def test_new_no_trade_below_threshold(params):
    out = decide([Candidate("A", 0.3)], set(), params)
    assert out.orders == []                       # τ=0.6 미만 → 무거래


def test_held_invalidation_sells(params):
    # 보유인데 점수가 exit_threshold(0.4) 아래 → 청산 제안
    out = decide([Candidate("A", 0.2)], {"A"}, params)
    assert out.orders[0].action is OrderAction.SELL


def test_held_strong_holds(params):
    out = decide([Candidate("A", 0.8)], {"A"}, params)
    assert out.orders[0].action is OrderAction.HOLD


def test_run_decision_uses_holdings(params):
    cands = [Candidate("005930", 0.9), Candidate("000660", 0.3)]
    out = run_decision(cands, ["000660"], params=params)
    by_code = {o.code: o.action for o in out.orders}
    assert by_code["005930"] is OrderAction.BUY   # 신규: 임계 통과
    assert by_code["000660"] is OrderAction.SELL  # 보유: 무효 임계 미달 → 청산


def test_notes_label(params):
    assert decide([], set(), params).notes == "rule_decider"
