"""
description:        사이클 1단계 — 후보 선별·워치리스트 구성
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date

import pandas as pd

from config.settings import load_params
from data import panel, screener
from data.features import eligible


def run_screening(
    prices: dict[str, pd.DataFrame],
    *,
    holdings: tuple[str, ...] = (),
    asof: date | None = None,
    params: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """워치리스트와 그 근거 패널을 함께 반환한다. 반환: (워치리스트, 패널).

    패널을 함께 내주는 이유는 사이클이 종목별 close·atr·adv20을 다시 계산하지 않게
    하기 위해서다 — 같은 값을 CycleScores 적재·사이징·유동성 한도가 나눠 쓴다.
    """
    sp = (params or load_params("risk_params")).get("screener", {})
    weights = {k: v for k, v in sp.items() if k.startswith("w_")}
    top_n = int(sp.get("top_n", 40))

    pnl = panel.build_panel(prices, asof=asof)
    if pnl.empty:
        return pd.DataFrame(columns=["score"]), pnl
    wl = screener.screen(
        pnl, weights=weights, top_n=top_n, holdings=holdings, eligible=eligible(pnl),
    )
    return wl, pnl


def select_watchlist(
    prices: dict[str, pd.DataFrame],
    *,
    holdings: tuple[str, ...] = (),
    asof: date | None = None,
    params: dict | None = None,
) -> pd.DataFrame:
    """워치리스트 DataFrame(index=code, score 내림차순)을 만든다."""
    return run_screening(prices, holdings=holdings, asof=asof, params=params)[0]
