"""후보 선별·워치리스트 (04-data 4.2). 운영과 백테스트가 같은 점수식을 쓴다.

제외 필터를 통과해 남은 종목 집합 안에서 각 지표를 **횡단면 백분위**로 바꿔 가중합한다.
원시값을 그대로 더하지 않는 이유는 지표마다 단위·분포가 달라 큰 값 하나가 점수를 삼키기
때문이다. 결측 지표는 중립(0.5)으로 둬 한 지표가 비었다고 종목이 통째로 탈락하지 않게 한다.

방향: 모멘텀·수급·거래대금 증가는 높을수록, 60일 실현변동성은 낮을수록 좋다.
가중치는 risk_params.toml [screener]. 합이 1.0이 아니어도 되며 여기서 재정규화한다.
"""
from __future__ import annotations

import pandas as pd

# 지표명 → 높을수록 좋은가 (04-data 4.2 네 항목)
_HIGHER_BETTER = {
    "momentum": True,      # 12-1 모멘텀
    "supply": True,        # 외국인·기관 20일 누적 순매수
    "value": True,         # 거래대금 증가(5일평균 ÷ 60일평균)
    "lowvol": False,       # 60일 실현변동성 — 낮을수록 가점
}


def _pct(s: pd.Series, higher_better: bool) -> pd.Series:
    return s.rank(pct=True, ascending=higher_better).fillna(0.5)


# 수급 누적 창 — 20일만 쓴다. 5일은 20일에 이미 포함된 구간이라 최근 5일에 이중 가중을
# 주는 셈인데, 그 강조가 예측력을 더하지 못했다(09-eval 9.5).
SUPPLY_COLS = ("supply20",)


def _supply_pct(panel: pd.DataFrame) -> pd.Series | None:
    """수급 백분위 — SUPPLY_COLS 각 창의 백분위 평균 (04-data 4.2).

    창이 둘 이상이면 각각 백분위로 바꾼 뒤 평균한다(한 창의 스케일이 다른 창을 잡아먹지
    않게 하는 표준 처리). 현재는 20일 하나만 쓴다.
    """
    cols = [c for c in SUPPLY_COLS if c in panel.columns]
    if not cols:
        return None
    return sum(_pct(panel[c], True) for c in cols) / len(cols)


def score(panel: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """종목별 종합 점수(0~1). panel: index=code, columns=지표명(일부 결측 허용).

    백분위는 *주어진 panel 안에서* 매겨진다 — 제외 필터를 통과한 집합을 넣어야 한다.
    """
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
    """워치리스트 = 상위 top_n + 보유 전부. 컬럼 score(내림차순).

    eligible: 제외 필터 통과 종목(미지정이면 panel 전체). 점수 백분위는 이 집합 안에서
    매기고, 보유 종목은 필터에서 빠졌더라도 워치리스트에 반드시 넣는다 — 청산 판단
    대상이기 때문이다(04-data 4.2).
    """
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
