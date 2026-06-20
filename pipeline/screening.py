"""사이클 1단계 — 후보 선별·워치리스트 (03-arch 3.1·04-data).

운영 패널(`data.panel`) → 스크리너(`data.screener`)를 묶어 *이번 사이클의 워치리스트*를
만든다: 유니버스에서 상위 top_n + 보유 종목 전부. 가중치·top_n·유동성 하한은
risk_params.toml [screener]에서 읽는다(그 시점 값으로 재현 가능하게).

이벤트 트리거 사이클은 이 스크리닝을 건너뛰고 워치리스트=보유로 좁힌다(3.1) → 그때는
`run_cycle`이 이 함수를 호출하지 않는다.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from config.settings import load_params
from data import panel, screener


def select_watchlist(
    prices: dict[str, pd.DataFrame],
    *,
    holdings: tuple[str, ...] = (),
    asof: date | None = None,
    min_value_traded: float | None = None,
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
        pnl,
        weights=weights,
        top_n=top_n,
        holdings=holdings,
        min_value_traded=min_value_traded,
    )
