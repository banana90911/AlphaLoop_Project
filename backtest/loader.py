"""백테스트 입력 로더 (backtest/loader). 캐시 parquet → 엔진용 prices dict.

엔진은 종목별 date 인덱스 OHLCV(+foreign_net/inst_net 선택)를 받는다. 캐시는 OHLCV와
수급(supply)을 별 파일로 저장(date 컬럼 보유)하므로 여기서 date를 인덱스로 세우고 수급을
병합한다. 실전 운영은 이 캐시를 읽지 않는다(data/cache 주석).
"""
from __future__ import annotations

import pandas as pd

from data import cache

_SUPPLY_COLS = ["inst_net", "foreign_net"]


def load_one(code: str) -> pd.DataFrame | None:
    """한 종목의 OHLCV(+수급)을 date 인덱스 DataFrame으로. 캐시 없으면 None."""
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
    """여러 종목 로드. (prices, markets) 반환. markets 미지정 종목은 default_market."""
    prices: dict[str, pd.DataFrame] = {}
    mk: dict[str, str] = {}
    for code in codes:
        df = load_one(code)
        if df is None:
            continue
        prices[code] = df
        mk[code] = (markets or {}).get(code, default_market)
    return prices, mk
