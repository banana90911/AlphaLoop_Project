"""후보 선별·워치리스트 (data/screener, 04-data §0c·P1-7).

특정 거래일의 종목별 지표 패널을 **횡단면 백분위**(필터 통과 집합 내 순위)로 정규화해
가중합 점수를 내고, 상위 top_n + 보유 종목 전부를 워치리스트로 만든다(05-risk).

방향: 모멘텀·수급·정배열은 높을수록, 변동성·밸류(PER 등)는 낮을수록 좋다.
결측 지표는 중립(0.5)으로 둬 한 지표 결측이 종목을 통째로 떨구지 않게 한다.
가중치는 risk_params.toml [screener](합 1.0 가정, 결측 지표 비중은 재정규화).
"""
from __future__ import annotations

import pandas as pd

# 지표명 → 높을수록 좋은가(ascending=True면 큰 값이 높은 백분위)
_DIRECTION = {
    "momentum": True,
    "supply": True,
    "alignment": True,
    "lowvol": False,   # 변동성은 낮을수록 좋음
    "value": False,    # PER 등은 낮을수록 좋음
}


def _percentile(s: pd.Series, higher_better: bool) -> pd.Series:
    return s.rank(pct=True, ascending=higher_better).fillna(0.5)


def score(panel: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """종목별 종합 점수(0~1). panel: index=code, columns=지표명(일부 결측 허용)."""
    total = pd.Series(0.0, index=panel.index)
    wsum = 0.0
    for col, higher_better in _DIRECTION.items():
        w = weights.get(f"w_{col}", 0.0)
        if w == 0:
            continue
        pct = _percentile(panel[col], higher_better) if col in panel.columns else 0.5
        total = total + w * pct
        wsum += w
    return total / wsum if wsum else total


def screen(
    panel: pd.DataFrame,
    *,
    weights: dict[str, float],
    top_n: int,
    holdings: tuple[str, ...] = (),
    min_value_traded: float | None = None,
) -> pd.DataFrame:
    """워치리스트 = 상위 top_n + 보유 전부. 컬럼 score(내림차순).

    min_value_traded: 'value_traded'(일평균 거래대금) 컬럼 기준 유동성 필터(보유는 면제).
    """
    pool = panel
    if min_value_traded is not None and "value_traded" in panel.columns:
        keep = panel["value_traded"] >= min_value_traded
        pool = panel[keep | panel.index.isin(holdings)]

    sc = score(pool, weights)
    watch = list(sc.sort_values(ascending=False).head(top_n).index)
    for h in holdings:                       # 보유 종목은 점수·유동성 무관 항상 포함
        if h not in watch and h in panel.index:
            watch.append(h)
    full = score(panel, weights)             # 보유가 풀에서 빠졌을 수 있으니 전체에서 점수
    return pd.DataFrame({"score": full[watch]}).sort_values("score", ascending=False)
