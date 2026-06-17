"""시장 레짐(추세) 필터 (backtest/regime, 10-1 하락장 방어).

핵심: 개별 종목이 좋아 보여도 **시장 전체가 하락 추세면 신규 진입을 멈춘다**(현금 대기).
지수 종가가 N일 이동평균 위면 상승추세(매수 허용), 아래면 하락추세(중단).

거친 단일지수 필터부터 시작한다(손잡이=trend_days 1개) — 섹터·베타 등 정교화는
손잡이가 늘어 과최적화(PBO)를 키우므로 효과를 게이트로 확인한 뒤에만 도입한다.
워밍업(MA 산출 전) 구간은 보수적으로 '하락추세 취급'하지 않도록 지수를 길게 받아 채운다.
"""
from __future__ import annotations

import pandas as pd

from data import indicators as ind


def uptrend(index_close: pd.Series, trend_days: int = 200) -> pd.Series:
    """지수 종가 > SMA(trend_days) → True(상승추세). index=date. 워밍업(NaN)은 False."""
    ma = ind.sma(index_close, trend_days)
    return (index_close > ma).fillna(False)


def market_trend(
    indexes: dict[str, pd.Series], trend_days: int = 200
) -> dict[str, pd.Series]:
    """시장별 지수 종가 → 시장별 상승추세 bool 시계열. engine.run(market_trend=)에 주입."""
    return {mk: uptrend(close, trend_days) for mk, close in indexes.items()}
