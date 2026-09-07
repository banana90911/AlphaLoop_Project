"""
description:        시장 지수 과거 시세 (yfinance, 벤치마크·레짐 라벨용)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import datetime

import pandas as pd
import yfinance as yf

# 시장 → yfinance 심볼
_SYMBOLS = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"}
# yfinance는 'Close'처럼 첫 글자를 대문자로 준다. 대소문자에 기대지 않고
# 받은 뒤 소문자로 맞춘 다음 이 순서로 고른다.
_COLS = ("open", "high", "low", "close", "volume")


class IndexHistoryError(RuntimeError):
    """지수 수신·파싱 실패 격리용."""


def _ymd_dash(s: str) -> str:
    """YYYYMMDD → YYYY-MM-DD (yfinance 형식)."""
    return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")


def fetch_index(market: str, start: str, end: str) -> pd.DataFrame:
    """시장 지수 OHLCV(date 컬럼)를 조회한다. start/end는 YYYYMMDD."""
    if market not in _SYMBOLS:
        raise IndexHistoryError(f"지원하지 않는 시장: {market}")
    raw = yf.download(_SYMBOLS[market], start=_ymd_dash(start), end=_ymd_dash(end),
                      progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        raise IndexHistoryError(f"{market} 지수 빈 응답")
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):       # 단일심볼도 (필드, 심볼) 튜플로 옴
        df.columns = [c[0] for c in df.columns]
    df.columns = [str(c).lower() for c in df.columns]
    missing = [c for c in _COLS if c not in df.columns]
    if missing:
        raise IndexHistoryError(f"{market} 지수 컬럼 없음: {missing} (받은 것: {list(df.columns)})")
    df = df[list(_COLS)]
    df.index = pd.to_datetime(df.index).date
    df = df.reset_index(names="date")
    return df
