"""
description:        후보 선별·워치리스트 (운영·백테스트 공용 점수식)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import pandas as pd

# 지표명 → 높을수록 좋은가 (04-data 4.2 네 항목)
_HIGHER_BETTER = {
    "momentum": True,      # 12-1 모멘텀
    "supply": True,        # 외국인·기관 20일 누적 순매수
    "value": True,         # 거래대금 증가(5일평균 ÷ 60일평균)
    "lowvol": False,       # 60일 실현변동성 — 낮을수록 가점
}


def _pct(s: pd.Series, higher_better: bool) -> pd.Series:
    """횡단면 백분위(결측은 중립 0.5)로 변환한다."""
    return s.rank(pct=True, ascending=higher_better).fillna(0.5)


# 수급 누적 창 — 20일만 쓴다(5일 이중가중이 예측력을 더하지 못했다, 09-eval 9.5).
SUPPLY_COLS = ("supply20",)


def _supply_pct(panel: pd.DataFrame) -> pd.Series | None:
    """수급 백분위 — SUPPLY_COLS 각 창의 백분위 평균을 반환한다."""
    cols = [c for c in SUPPLY_COLS if c in panel.columns]
    if not cols:
        return None
    return sum(_pct(panel[c], True) for c in cols) / len(cols)


def score(panel: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """종목별 종합 점수(0~1)를 계산한다(백분위는 panel 안에서 매김)."""
    total = pd.Series(0.0, index=panel.index)
    wsum = 0.0
    for name, higher in _HIGHER_BETTER.items():
        w = weights.get(f"w_{name}", 0.0)
        if w == 0:
            continue
        if name == "supply":
            pct = _supply_pct(panel)
        else:
            pct = _pct(panel[name], higher) if name in panel.columns else None
        total = total + w * (pd.Series(0.5, index=panel.index) if pct is None else pct)
        wsum += w
    return total / wsum if wsum else total


def screen(
    panel: pd.DataFrame,
    *,
    weights: dict[str, float],
    top_n: int,
    holdings: tuple[str, ...] = (),
    eligible: pd.Index | None = None,
) -> pd.DataFrame:
    """워치리스트(상위 top_n + 보유 전부, score 내림차순)를 만든다."""
    pool = panel if eligible is None else panel.loc[panel.index.intersection(eligible)]
    if pool.empty:
        pool = panel
    sc = score(pool, weights)
    watch = list(sc.sort_values(ascending=False).head(top_n).index)
    for h in holdings:
        if h not in watch and h in panel.index:
            watch.append(h)
    if not watch:
        return pd.DataFrame(columns=["score"])
    # 보유가 풀에서 빠졌을 수 있으니 워치리스트 전체를 포함한 집합에서 점수를 다시 낸다
    full = score(panel.loc[panel.index.intersection(pool.index.union(pd.Index(watch)))], weights)
    return pd.DataFrame({"score": full.reindex(watch)}).sort_values("score", ascending=False)
