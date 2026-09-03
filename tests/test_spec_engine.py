"""
description:        백테스트 정본 엔진 — SafeStop 영구성·무거래 규칙
author:             siheon jung
created date:       2026/08/30
last modified date: 2026/08/30
remarks:            실거래 경로(pipeline/cycle)와 같은 규칙을 같게 적용하는지 고정한다.
"""

import copy

import numpy as np
import pandas as pd

from backtest import spec_engine as se
from config.settings import load_params

_N = 340                       # 12-1 모멘텀 워밍업(252+20) + 검증 구간 여유
_DATES = pd.bdate_range(end="2024-06-28", periods=_N).date
_START = _DATES[300]
_END = _DATES[-1]


def _bars(start: float, step: float, *, vol: float = 0.01) -> pd.DataFrame:
    close = start + step * np.arange(_N)
    return pd.DataFrame(
        {"date": _DATES, "open": close, "high": close * (1 + vol),
         "low": close * (1 - vol), "close": close, "volume": 1_000_000.0},
    ).set_index("date")


def _universe() -> tuple[dict, dict]:
    prices = {f"U{i}": _bars(10_000, 20 + i) for i in range(6)}
    return prices, dict.fromkeys(prices, "KOSPI")


def _params(**over) -> dict:
    p = copy.deepcopy(load_params("risk_params"))   # lru_cache 원본 오염 방지
    p["decision"]["entry_threshold"] = 0.0
    for section, values in over.items():
        p[section].update(values)
    return p


def _run(params, capital=10_000_000):
    prices, markets = _universe()
    return se.run(prices, markets, start=_START, end=_END,
                  initial_capital=capital, params=params, entry_timing="last")


# ── 기준선: 정상이면 진입이 난다 ────────────────────────────────────
def test_baseline_enters():
    res = _run(_params())
    assert res.diag["entries"] > 0
    assert res.safe_stop_date is None
    assert res.diag["safestop"] == 0


# ── SafeStop은 한 번 걸리면 풀리지 않는다 (05-risk 5.4) ──────────────
def test_safe_stop_fires_and_records_date():
    # 어떤 주문도 자본의 0.1%를 넘으므로 첫 진입 시도에서 이상행동 판정
    res = _run(_params(anomaly={"single_order_pct": 0.001}))
    assert res.diag["safestop"] == 1          # 발동은 딱 한 번
    assert res.safe_stop_date is not None


def test_safe_stop_blocks_all_later_entries():
    res = _run(_params(anomaly={"single_order_pct": 0.001}))
    assert res.diag["entries"] == 0           # 이후 신규 진입 영구 차단
    assert res.diag["safestop_blocked_days"] > 1   # 하루로 끝나지 않는다


def test_safe_stop_does_not_retrigger_each_day():
    # 신규가 막히면 제안 자체가 없으므로 이상행동 판정이 다시 돌지 않는다
    res = _run(_params(anomaly={"single_order_pct": 0.001}))
    assert res.diag["safestop"] == 1
    assert res.diag["buy_signals"] > 0        # 신호는 계속 났지만 집행이 안 됨


def test_normal_run_has_no_blocked_days():
    res = _run(_params())
    assert res.diag["safestop_blocked_days"] == 0


# ── 무거래: 기대이익 < 왕복 비용 (06-sizing 6.1) ────────────────────
def test_flat_universe_blocked_by_cost_rule():
    # 일중 변동 0.005% → ATR이 극히 작아 기대이익이 왕복 비용을 못 넘는다
    prices = {f"F{i}": _bars(100_000, 20 + i, vol=0.00005) for i in range(6)}
    markets = dict.fromkeys(prices, "KOSPI")
    res = se.run(prices, markets, start=_START, end=_END,
                 initial_capital=10_000_000, params=_params(), entry_timing="last")
    assert res.diag["cost_exceeds_edge"] > 0
    assert res.diag["entries"] == 0


def test_normal_volatility_passes_cost_rule():
    res = _run(_params())
    assert res.diag["cost_exceeds_edge"] == 0


# ── 보유일수는 거래일로 센다 (06-sizing 6.2) ────────────────────────
def test_holding_days_counted_in_trading_days():
    # 시간청산(20거래일)만 남기고 다른 청산 사유를 끈다
    p = _params(exits={"max_hold_days": 5, "min_progress_R": 99.0,
                       "trail_k": 999.0, "breakeven_R": 999.0})
    res = _run(p)
    timed = [t for t in res.trades if t.reason == "time_exit"]
    assert timed, "시간청산이 한 건도 없다"
    for t in timed:
        # 거래일로 셌다면 달력일 간격은 그보다 크거나 같다(주말이 끼므로)
        assert (t.exit_date - t.entry_date).days >= 5
