"""지표 — 알려진 입력으로 값 검증 (data/indicators, 04-data §0c)."""
import numpy as np
import pandas as pd

from data import indicators as ind


def test_sma():
    s = pd.Series([1, 2, 3, 4, 5])
    assert ind.sma(s, 3).iloc[-1] == 4.0          # (3+4+5)/3


def test_momentum():
    s = pd.Series([100, 110, 121])
    assert abs(ind.momentum(s, 1).iloc[-1] - 0.1) < 1e-9   # 121/110-1
    assert abs(ind.momentum(s, 2).iloc[-1] - 0.21) < 1e-9  # 121/100-1


def test_atr_constant_range():
    # 매일 고저폭 10, 갭 없음 → ATR=10
    high = pd.Series([110, 110, 110, 110])
    low = pd.Series([100, 100, 100, 100])
    close = pd.Series([105, 105, 105, 105])
    assert ind.atr(high, low, close, n=2).iloc[-1] == 10.0


def test_realized_vol_zero_when_flat():
    s = pd.Series([100.0] * 10)
    assert ind.realized_vol(s, 5).iloc[-1] == 0.0


def test_rsi_all_gains_is_100():
    s = pd.Series(np.arange(1, 30, dtype=float))  # 단조 증가 → 손실 0 → RSI 100
    assert ind.rsi(s, 14).iloc[-1] == 100.0


def test_alignment_full_uptrend():
    # 단조 증가 → MA5>MA20>MA60 → 1.0
    s = pd.Series(np.arange(1, 100, dtype=float))
    assert ind.alignment_score(s).iloc[-1] == 1.0


def test_alignment_full_downtrend():
    s = pd.Series(np.arange(100, 1, -1, dtype=float))
    assert ind.alignment_score(s).iloc[-1] == 0.0


def test_pct_from_high():
    s = pd.Series([100, 120, 90])               # 고점 120, 현재 90 → -25%
    assert abs(ind.pct_from_high(s, 3).iloc[-1] - (-0.25)) < 1e-9


def test_net_supply_cumsum():
    flow = pd.Series([100, -50, 200, -30])
    assert ind.net_supply(flow, 3).iloc[-1] == 120   # -50+200-30
