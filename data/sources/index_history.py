"""시장 지수 과거 시세 (data/sources/index_history). 하락장 방어(레짐) 판단용.

코스피·코스닥 지수는 KIS/네이버 대신 yfinance로 받는다(지수는 yfinance가 단순·안정).
시장 추세 필터(backtest/regime)의 입력 — 지수가 추세 위일 때만 신규 진입을 허용한다.
워밍업(이동평균) 확보를 위해 종목보다 앞선 시작일로 받는 것을 권장한다.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import yfinance as yf

# 시장 → yfinance 심볼
_SYMBOLS = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"}
_COLS = {"Open": "open", "High": "high", "Low": "low", "Close": "close",
         "Volume": "volume"}


class IndexHistoryError(RuntimeError):
    """지수 수신·파싱 실패 격리용."""


def _ymd_dash(s: str) -> str:
    """YYYYMMDD → YYYY-MM-DD (yfinance 형식)."""
    return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")


def fetch_index(market: str, start: str, end: str) -> pd.DataFrame:
    """시장 지수 OHLCV(date 컬럼·표준컬럼). start/end는 YYYYMMDD."""
    if market not in _SYMBOLS:
        raise IndexHistoryError(f"지원하지 않는 시장: {market}")
    raw = yf.download(_SYMBOLS[market], start=_ymd_dash(start), end=_ymd_dash(end),
                      progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        raise IndexHistoryError(f"{market} 지수 빈 응답")
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):       # 단일심볼도 (필드, 심볼) 튜플로 옴
        df.columns = [c[0] for c in df.columns]
    try:
        df = df.rename(columns=_COLS)[list(_COLS.values())]
    except KeyError as e:
        raise IndexHistoryError(f"{market} 지수 컬럼 이상: {e}") from e
    df.index = pd.to_datetime(df.index).date
    df = df.reset_index(names="date")
    return df
