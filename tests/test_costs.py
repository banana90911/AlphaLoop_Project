"""
description:        거래비용·세금·슬리피지 — 날짜별 세율·매수/매도 비대칭 (core/costs, 09-eval 9.1).
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date

import pytest

from core import costs

_P = {
    "sell_tax": [
        {"effective_from": "2023-01-01", "market": "KOSPI", "rate": 0.0020},
        {"effective_from": "2024-01-01", "market": "KOSPI", "rate": 0.0018},
        {"effective_from": "2025-01-01", "market": "KOSPI", "rate": 0.0015},
        {"effective_from": "2025-12-29", "market": "KOSPI", "rate": 0.0020},
        {"effective_from": "2023-01-01", "market": "KOSDAQ", "rate": 0.0020},
    ],
    "brokerage": {"rate": 0.00015},
    "slippage": {"rate": 0.0010},
}


def test_buy_has_no_tax():
    c = costs.trade_cost(50_000, 10, "buy", "KOSPI", date(2026, 6, 1), params=_P)
    assert c["tax"] == 0.0
    # 수수료 50만*0.00015=75, 슬리피지 50만*0.001=500
    assert c["commission"] == 75.0
    assert c["slippage"] == 500.0
    assert c["total"] == 575.0


def test_sell_includes_tax_2026():
    c = costs.trade_cost(50_000, 10, "sell", "KOSPI", date(2026, 6, 1), params=_P)
    assert c["tax"] == 500_000 * 0.0020       # 환원 후 0.20%


def test_date_dependent_tax_rate():
    # 같은 거래가 연도별로 다른 세율
    def tax(d):
        return costs.trade_cost(50_000, 10, "sell", "KOSPI", d, params=_P)["tax"]
    assert tax(date(2023, 6, 1)) == 500_000 * 0.0020
    assert tax(date(2024, 6, 1)) == 500_000 * 0.0018
    assert tax(date(2025, 6, 1)) == 500_000 * 0.0015
    assert tax(date(2025, 12, 29)) == 500_000 * 0.0020   # 환원 시행일


def test_unknown_market_raises():
    with pytest.raises(costs.CostError):
        costs.trade_cost(50_000, 10, "sell", "KONEX", date(2026, 6, 1), params=_P)


def test_stress_doubles_slippage():
    base = costs.trade_cost(50_000, 10, "buy", "KOSPI", date(2026, 6, 1), params=_P)
    stressed = costs.trade_cost(50_000, 10, "buy", "KOSPI", date(2026, 6, 1), stress=2.0, params=_P)
    assert stressed["slippage"] == 2 * base["slippage"]


def test_round_trip_sums_both_legs():
    rt = costs.round_trip_cost(50_000, 55_000, 10, "KOSPI",
                               date(2026, 1, 2), date(2026, 1, 10), params=_P)
    buy = costs.trade_cost(50_000, 10, "buy", "KOSPI", date(2026, 1, 2), params=_P)["total"]
    sell = costs.trade_cost(55_000, 10, "sell", "KOSPI", date(2026, 1, 10), params=_P)["total"]
    assert rt == buy + sell


def test_invalid_side():
    with pytest.raises(ValueError):
        costs.trade_cost(50_000, 10, "hold", "KOSPI", date(2026, 6, 1), params=_P)


# ── 무거래 판정: 기대이익 vs 왕복 거래비용 (06-sizing 6.1) ──

def _edge(entry, stop, qty=10, reward_r=1.5, d=date(2026, 6, 1)):
    return costs.entry_edge(entry, stop, qty, "KOSPI", d, reward_r=reward_r, params=_P)


def test_normal_trade_has_positive_edge():
    # ATR이 주가의 2% → 손절폭 4%, 기대이익 6%. 왕복 비용 0.43%보다 훨씬 크다
    e = _edge(50_000, 48_000)
    assert e["net_edge"] > 0
    assert e["expected_gain"] > e["estimated_cost"]
    assert e["reward_risk_ratio"] > 1.0


def test_expected_gain_is_reward_r_times_risk():
    e = _edge(50_000, 48_000, qty=10, reward_r=1.5)
    assert e["expected_gain"] == 1.5 * (50_000 - 48_000) * 10


def test_tiny_stop_width_fails_cost_hurdle():
    # 손절폭이 주가의 0.05% → 기대이익 0.075% < 왕복 비용 0.43% → 무거래
    e = _edge(50_000, 49_975)
    assert e["net_edge"] < 0
    assert e["reward_risk_ratio"] < 0


def test_edge_sign_flips_at_threshold():
    # 임계 부근에서 부호가 바뀐다(왕복 0.43% ÷ reward_r 1.5 ≈ 손절폭 0.287%)
    near_below = _edge(100_000, 100_000 * (1 - 0.0028))
    near_above = _edge(100_000, 100_000 * (1 - 0.0032))
    assert near_below["net_edge"] < 0 < near_above["net_edge"]


def test_edge_scales_with_quantity():
    one, ten = _edge(50_000, 48_000, qty=1), _edge(50_000, 48_000, qty=10)
    assert abs(ten["net_edge"] - 10 * one["net_edge"]) < 1e-6
    # R 배수는 수량에 무관하다 — 종목·금액이 달라도 비교되는 잣대
    assert abs(ten["reward_risk_ratio"] - one["reward_risk_ratio"]) < 1e-9


def test_invalid_inputs_return_zero():
    assert _edge(50_000, 50_000)["net_edge"] == 0.0      # 손절폭 0
    assert _edge(50_000, 51_000)["net_edge"] == 0.0      # 손절가가 진입가 위
    assert _edge(50_000, 48_000, qty=0)["net_edge"] == 0.0
