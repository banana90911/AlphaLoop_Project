"""성과 지표·벤치마크 (eval/metrics, 10-1). 전부 코드.

equity 곡선(시간 오름차순)에서 누적수익·CAGR·Sharpe·Sortino·MDD·Calmar를 산출한다.
판정은 net(거래비용·세금·LLM비 차감) equity 기준(engine이 이미 비용 반영).
벤치마크 4종(매수후보유·현금·균등가중·단순모멘텀)과 비교해 결정가치를 분리한다(10-3).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_PPY = 252  # 연간 거래일


def daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def total_return(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] == 0:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series, ppy: int = _PPY) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    years = (len(equity) - 1) / ppy
    if years <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0)


def sharpe(returns: pd.Series, rf: float = 0.0, ppy: int = _PPY) -> float:
    """연율 Sharpe. rf=연 무위험수익률. 표준편차 0이면 0."""
    if len(returns) < 2:
        return 0.0
    sd = returns.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return 0.0
    excess = returns - rf / ppy
    return float(np.sqrt(ppy) * excess.mean() / sd)


def sortino(returns: pd.Series, rf: float = 0.0, ppy: int = _PPY) -> float:
    """하방변동성 기준 Sortino. 하방 표본 없으면 0."""
    if len(returns) < 2:
        return 0.0
    downside = returns[returns < 0]
    dd = downside.std(ddof=1)
    if len(downside) < 2 or dd == 0 or np.isnan(dd):
        return 0.0
    return float(np.sqrt(ppy) * (returns.mean() - rf / ppy) / dd)


def max_drawdown(equity: pd.Series) -> float:
    """최대 낙폭(음수). peak 대비 최저."""
    if len(equity) < 2:
        return 0.0
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def calmar(equity: pd.Series, ppy: int = _PPY) -> float:
    """CAGR / |MDD|. MDD 0이면 0."""
    mdd = max_drawdown(equity)
    return cagr(equity, ppy) / abs(mdd) if mdd < 0 else 0.0


def summary(equity: pd.Series, rf: float = 0.0, ppy: int = _PPY) -> dict[str, float]:
    r = daily_returns(equity)
    return {
        "total_return": total_return(equity),
        "cagr": cagr(equity, ppy),
        "sharpe": sharpe(r, rf, ppy),
        "sortino": sortino(r, rf, ppy),
        "max_drawdown": max_drawdown(equity),
        "calmar": calmar(equity, ppy),
    }


# ── 벤치마크 (10-3: 4종 전부 초과해야 방향성 게이트 통과) ──────────────
def buy_and_hold_equity(price: pd.Series, capital: float = 1.0) -> pd.Series:
    """단일 자산 매수후보유(코스피 지수 등). price 첫값 대비 정규화."""
    if price.empty or price.iloc[0] == 0:
        return pd.Series(dtype=float)
    return capital * price / price.iloc[0]


def cash_equity(index: pd.Index, capital: float = 1.0) -> pd.Series:
    """현금 보유(무수익) 벤치마크."""
    return pd.Series(capital, index=index, dtype=float)


def equal_weight_equity(prices: dict[str, pd.Series], capital: float = 1.0) -> pd.Series:
    """균등가중 매수후보유(워치리스트). 종목별 정규화 평균."""
    if not prices:
        return pd.Series(dtype=float)
    norm = [p / p.iloc[0] for p in prices.values() if not p.empty and p.iloc[0] != 0]
    if not norm:
        return pd.Series(dtype=float)
    return capital * pd.concat(norm, axis=1).mean(axis=1)


def momentum_equity(price: pd.Series, lookback: int = 200, capital: float = 1.0) -> pd.Series:
    """단순 모멘텀(SMA 크로스) 벤치마크 — 가격 > SMA(lookback)이면 보유, 아니면 현금.

    지수 등 단일 자산에 적용. 전일 신호로 당일 보유를 결정해 룩어헤드를 차단한다
    (`signal.shift(1)`). 워밍업 구간(SMA 결측)은 현금(0) 처리.
    """
    if price.empty or len(price) < 2:
        return pd.Series(dtype=float)
    sma = price.rolling(lookback).mean()
    hold = (price > sma).shift(1, fill_value=False).astype(float)   # 전일 신호 → 당일 보유
    strat_ret = price.pct_change().fillna(0.0) * hold
    return capital * (1.0 + strat_ret).cumprod()
