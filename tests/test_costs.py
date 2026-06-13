"""거래비용·세금·슬리피지 — 날짜별 세율·매수/매도 비대칭 (core/costs, 10-1)."""
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
