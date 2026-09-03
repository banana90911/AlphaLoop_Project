"""
description:        피처·제외 필터 단일 정의 (운영·백테스트 공용)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import numpy as np
import pandas as pd

from data import indicators as ind

# 04-data 4.2 제외 필터 임계 (문서 고정값 — 튜닝 손잡이가 아니다)
MIN_PRICE = 2_000.0          # 동전주 제외
MIN_ADV20 = 3_000_000_000.0  # 최근 20일 평균 거래대금 30억 미만 제외

# 스크리너가 읽는 패널 컬럼(04-data 4.2 네 항목)
SCREEN_COLS = ["momentum", "supply20", "value", "lowvol"]


def build_features(df: pd.DataFrame, *, supply_windows: tuple[int, int] = (5, 20)) -> pd.DataFrame:
    """종목 OHLCV(+수급)로부터 스크리너용 피처 시계열을 만든다."""
    close, high, low = df["close"], df["high"], df["low"]
    out = pd.DataFrame(index=df.index)
    for col in ("open", "high", "low", "close"):
        if col in df:
            out[col] = df[col]
    out["momentum"] = ind.momentum(close, 252, skip=20)
    out["atr"] = ind.atr(high, low, close, 20)
    out["lowvol"] = ind.realized_vol(close, 60)
    out["ma20"] = ind.sma(close, 20)
    out["ma60"] = ind.sma(close, 60)
    out["alignment"] = alignment(close, out["ma20"], out["ma60"])
    turnover = close * df["volume"]
    out["adv20"] = turnover.rolling(20).mean()
    out["value"] = turnover.rolling(5).mean() / turnover.rolling(60).mean()
    w_short, w_long = supply_windows
    if "foreign_net" in df or "inst_net" in df:
        flow = (df.get("foreign_net", pd.Series(np.nan, index=df.index)).fillna(0)
                + df.get("inst_net", pd.Series(np.nan, index=df.index)).fillna(0))
        out["supply5"] = flow.rolling(w_short).sum()
        out["supply20"] = flow.rolling(w_long).sum()
    else:
        out["supply5"] = np.nan
        out["supply20"] = np.nan
    return out


def alignment(close: pd.Series, ma20: pd.Series, ma60: pd.Series) -> pd.Series:
    """정배열 점수 0·0.5·1을 계산한다(종가>20일선, 20일선>60일선)."""
    return ((close > ma20).astype(float) + (ma20 > ma60).astype(float)) / 2.0


def eligible(panel: pd.DataFrame) -> pd.Index:
    """제외 필터(동전주·거래대금·워밍업)를 통과한 종목 인덱스를 반환한다."""
    if panel.empty:
        return panel.index[:0]
    close, adv, mom = panel.get("close"), panel.get("adv20"), panel.get("momentum")
    if close is None or adv is None or mom is None:
        return panel.index
    ok = (
        close.notna() & (close >= MIN_PRICE)
        & adv.notna() & (adv >= MIN_ADV20)
        & mom.notna()                       # 12-1 모멘텀 워밍업 = 상장 경과일 대용
    )
    return panel.index[ok.fillna(False)]
