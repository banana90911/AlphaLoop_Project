"""사이클 1단계 — 후보 선별·워치리스트 (03-arch 3-1·04-data 4.2).

운영 패널(`data.panel`) → 스크리너(`data.screener`)를 묶어 *그날의 워치리스트*를 만든다:
제외 필터를 통과한 집합에서 상위 top_n + 보유 종목 전부. 가중치·top_n은
risk_params.toml [screener]에서 읽는다(그 시점 값으로 재현 가능하게).

제외 필터(동전주·거래대금 하한·워밍업)는 `data.features.eligible`이 판정한다 — 점수
백분위의 모집단이 곧 필터 통과 집합이라는 04-data 4.2 정의를 그대로 따른다.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from config.settings import load_params
from data import panel, screener
from data.features import eligible


def select_watchlist(
    prices: dict[str, pd.DataFrame],
    *,
    holdings: tuple[str, ...] = (),
    asof: date | None = None,
    params: dict | None = None,
) -> pd.DataFrame:
    """워치리스트 DataFrame(index=code, score 내림차순). params 미지정 시 toml [screener]."""
    sp = (params or load_params("risk_params")).get("screener", {})
    weights = {k: v for k, v in sp.items() if k.startswith("w_")}
    top_n = int(sp.get("top_n", 40))

    pnl = panel.build_panel(prices, asof=asof)
    if pnl.empty:
        return pd.DataFrame(columns=["score"])
    return screener.screen(
        pnl, weights=weights, top_n=top_n, holdings=holdings, eligible=eligible(pnl),
    )
