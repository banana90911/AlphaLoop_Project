"""코드계산 지표 (Tier0c, 04-data §0c). 전부 결정론(LLM 미관여).

가격·수급 시계열(pandas)에서 모멘텀·ATR·변동성·RSI·정배열·52주 고저·수급 누적을 산출한다.
스크리너(점수화)·사이징·청산(ATR)이 공용으로 쓰는 기반. 별도 프레임워크 없이 numpy/pandas.

입력은 시간 오름차순 정렬된 Series 가정. NaN(워밍업 구간)은 그대로 둔다(백테스트가 충분한
선행 데이터 확보 후 진입 — 룩어헤드/워밍업은 engine이 관리).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def momentum(close: pd.Series, n: int) -> pd.Series:
    """n거래일 수익률 = close_t / close_{t-n} − 1."""
    return close / close.shift(n) - 1.0


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 20) -> pd.Series:
    """ATR(n) — True Range의 단순이동평균(재현성 우선, Wilder 대신 SMA). 기본 20(05-risk)."""
    return true_range(high, low, close).rolling(n).mean()


def realized_vol(close: pd.Series, n: int = 20, *, annualize: bool = True) -> pd.Series:
    """실현변동성 = 일간수익률 표준편차(연율화 옵션, 252거래일)."""
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
    """정배열 점수 0·0.5·1: (MA_fast>MA_mid) + (MA_mid>MA_slow) 평균. 추세 정렬 강도."""
    mf, mm, ms = sma(close, fast), sma(close, mid), sma(close, slow)
    return ((mf > mm).astype(float) + (mm > ms).astype(float)) / 2.0


def rolling_high(s: pd.Series, n: int = 252) -> pd.Series:
    return s.rolling(n).max()


def rolling_low(s: pd.Series, n: int = 252) -> pd.Series:
    return s.rolling(n).min()


def pct_from_high(close: pd.Series, n: int = 252) -> pd.Series:
    """52주 고점 대비 위치(−값 = 고점 아래 %). 신고가 근접/이격 판정."""
    return close / rolling_high(close, n) - 1.0


def net_supply(flow: pd.Series, n: int) -> pd.Series:
    """수급 순매수(외국인·기관 등)의 n거래일 누적합. 양수=순매수 우위."""
    return flow.rolling(n).sum()
