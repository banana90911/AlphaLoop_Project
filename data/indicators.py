"""
description:        기술적 지표 계산 (모멘텀·ATR·변동성·RSI 등, 전부 결정론)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    """단순이동평균(n)."""
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """지수이동평균(n)."""
    return s.ewm(span=n, adjust=False).mean()


def momentum(close: pd.Series, n: int, skip: int = 0) -> pd.Series:
    """n거래일 모멘텀 = close_{t-skip} / close_{t-n} − 1."""
    return close.shift(skip) / close.shift(n) - 1.0


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range(당일 변동폭과 전일 종가 갭 중 최댓값)를 계산한다."""
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 20) -> pd.Series:
    """ATR(n) — True Range의 단순이동평균."""
    return true_range(high, low, close).rolling(n).mean()


def realized_vol(close: pd.Series, n: int = 20, *, annualize: bool = True) -> pd.Series:
    """실현변동성 = 일간수익률 표준편차(연율화 옵션)."""
    vol = close.pct_change().rolling(n).std()
    return vol * np.sqrt(252) if annualize else vol


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI(n) — Wilder 평활(EWM alpha=1/n)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def alignment_score(close: pd.Series, fast: int = 5, mid: int = 20, slow: int = 60) -> pd.Series:
    """정배열 점수 0·0.5·1(추세 정렬 강도)을 계산한다."""
    mf, mm, ms = sma(close, fast), sma(close, mid), sma(close, slow)
    return ((mf > mm).astype(float) + (mm > ms).astype(float)) / 2.0


def rolling_high(s: pd.Series, n: int = 252) -> pd.Series:
    """n거래일 최고값."""
    return s.rolling(n).max()


def rolling_low(s: pd.Series, n: int = 252) -> pd.Series:
    """n거래일 최저값."""
    return s.rolling(n).min()


def pct_from_high(close: pd.Series, n: int = 252) -> pd.Series:
    """52주 고점 대비 위치(음수=고점 아래 %)를 계산한다."""
    return close / rolling_high(close, n) - 1.0


def net_supply(flow: pd.Series, n: int) -> pd.Series:
    """수급 순매수(외국인·기관 등)의 n거래일 누적합을 계산한다."""
    return flow.rolling(n).sum()
