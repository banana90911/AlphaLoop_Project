"""
description:        백테스트 입력 로더 (parquet 캐시 → 엔진용 prices dict)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import pandas as pd

from data import cache

_SUPPLY_COLS = ["inst_net", "foreign_net"]


def load_one(code: str) -> pd.DataFrame | None:
    """한 종목의 OHLCV(+수급)를 date 인덱스 DataFrame으로 반환한다(캐시 없으면 None)."""
    ohlcv = cache.load(f"ohlcv_{code}")
    if ohlcv is None or ohlcv.empty:
        return None
    df = ohlcv.set_index("date").sort_index()
    supply = cache.load(f"supply_{code}")
    if supply is not None and not supply.empty:
        s = supply.set_index("date")[_SUPPLY_COLS]
        df = df.join(s, how="left")
    return df


def load_prices(
    codes: list[str], markets: dict[str, str] | None = None, *, default_market: str = "KOSPI"
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """여러 종목을 로드해 (prices, markets)를 반환한다."""
    prices: dict[str, pd.DataFrame] = {}
    mk: dict[str, str] = {}
    for code in codes:
        df = load_one(code)
        if df is None:
            continue
        prices[code] = df
        mk[code] = (markets or {}).get(code, default_market)
    return prices, mk
