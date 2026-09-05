"""
description:        사이클 1단계 — 워치리스트 선별·보유 포함·이벤트 분기 (pipeline/screening·cycle).
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date

import numpy as np
import pandas as pd

from pipeline import cycle, screening


def _series(start: float, step: float, n: int = 300, end: date = date(2024, 6, 28)) -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=n).date
    close = start + step * np.arange(n)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1_000_000.0},
        index=pd.Index(idx, name="date"),
    )


def _universe() -> dict[str, pd.DataFrame]:
    # 모멘텀 순: UP1 > UP2 > FLAT > DN
    return {
        "UP1": _series(1000, 20), "UP2": _series(1000, 10),
        "FLAT": _series(1000, 0.0), "DN": _series(2000, -5),
    }


def test_select_watchlist_ranks_by_score():
    wl = screening.select_watchlist(_universe())
    assert wl.index[0] == "UP1"                # 모멘텀 최강이 1위
    assert wl["score"].is_monotonic_decreasing


def test_holdings_always_included():
    # top_n=1로 좁혀도 보유(DN)는 포함
    params = {"screener": {"w_momentum": 1.0, "top_n": 1}}
    wl = screening.select_watchlist(_universe(), holdings=("DN",), params=params)
    assert "DN" in wl.index


def test_empty_prices_returns_empty():
    wl = screening.select_watchlist({})
    assert wl.empty


def test_cycle_step1_runs_with_market_data(conn):
    res = cycle.run(conn, market_data=_universe())
    status = conn.execute(
        'SELECT status FROM cycles WHERE cycle_id=%s', (res.cycle_id,)
    ).fetchone()["status"]
    assert status == "recorded"
    assert set(res.watchlist)                      # 워치리스트 채워짐
