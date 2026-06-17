"""시장 레짐(추세) 필터 — 지수 SMA 기준 상승/하락 판정 (backtest/regime)."""
from datetime import date, timedelta

import pandas as pd

from backtest import regime


def _close(vals: list[float]) -> pd.Series:
    idx = [date(2023, 1, 1) + timedelta(days=i) for i in range(len(vals))]
    return pd.Series(vals, index=idx)


def test_uptrend_above_ma_true():
    s = _close([100 + i for i in range(250)])           # 우상향
    assert bool(regime.uptrend(s, trend_days=200).iloc[-1]) is True


def test_downtrend_below_ma_false():
    s = _close([100 - i * 0.1 for i in range(250)])     # 우하향
    assert bool(regime.uptrend(s, trend_days=200).iloc[-1]) is False


def test_warmup_is_false():
    s = _close([100 + i for i in range(50)])            # 200일 못 채움
    assert not regime.uptrend(s, trend_days=200).any()  # 워밍업 전부 False


def test_market_trend_per_market():
    up = _close([100 + i for i in range(250)])
    dn = _close([100 - i * 0.1 for i in range(250)])
    mt = regime.market_trend({"KOSPI": up, "KOSDAQ": dn}, 200)
    assert bool(mt["KOSPI"].iloc[-1]) is True
    assert bool(mt["KOSDAQ"].iloc[-1]) is False
