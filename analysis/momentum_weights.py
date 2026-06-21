"""모멘텀 2단계 IC 테스트 (탐색용).

① 단계1: 6·9·12개월 단독(스킵 1개월) — 어느 룩백이 미래수익을 잘 맞히나.
② 단계2: 그 셋을 섞는 가중치 후보들 — 섞는 게 12개월 단독보다 나은가.

블렌드 방식: 각 룩백을 횡단면 백분위(순위)로 바꿔 가중합 → 그 합의 Rank IC.
지표: mean IC(예측력), IC IR(=mean/std, 안정성), hit(IC>0 비율).
사용: .venv/bin/python -m analysis.momentum_weights
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import loader
from data import cache

REBAL = 21
MIN_NAMES = 30
HORIZONS = [21, 63]
SKIP = 20                                  # 최근 1개월 제외(직전 그리드에서 최선)
LB = {"6개월": 126, "9개월": 189, "12개월": 252}

# 단계2 가중치 후보(룩백라벨→비중, 합 1.0). 직전 결과상 6개월은 약하므로 장기가중도 본다.
BLENDS = {
    "12단독":         {"12개월": 1.0},
    "9+12 동등":      {"9개월": 0.5, "12개월": 0.5},
    "9+12 장기가중":   {"9개월": 0.4, "12개월": 0.6},
    "6+9+12 동등":    {"6개월": 1/3, "9개월": 1/3, "12개월": 1/3},
    "6+9+12 장기가중":  {"6개월": 0.2, "9개월": 0.3, "12개월": 0.5},
}


def _stats(arr: list[float]) -> tuple[float, float, float, int]:
    a = np.array([x for x in arr if not np.isnan(x)])
    if len(a) == 0:
        return float("nan"), float("nan"), float("nan"), 0
    sd = a.std(ddof=1)
    return a.mean(), (a.mean() / sd if sd else float("nan")), (a > 0).mean(), len(a)


def main() -> None:
    uni = cache.load("universe")
    prices, _ = loader.load_prices(uni["code"].tolist())
    print(f"종목 {len(prices)}개 로드 (스킵 {SKIP}일 고정)\n")

    feats: dict[str, pd.DataFrame] = {}
    for code, df in prices.items():
        if df.empty or "close" not in df:
            continue
        c = df["close"]
        v = pd.DataFrame(index=c.index)
        for lab, lb in LB.items():
            v[lab] = c.shift(SKIP) / c.shift(lb) - 1.0
        for h in HORIZONS:
            v[f"fwd{h}"] = c.shift(-h) / c - 1.0
        feats[code] = v

    sample = sorted({d for v in feats.values() for d in v.index})[::REBAL]

    single = {h: {lab: [] for lab in LB} for h in HORIZONS}
    blend = {h: {name: [] for name in BLENDS} for h in HORIZONS}

    for d in sample:
        rows = {code: v.loc[d] for code, v in feats.items() if d in v.index}
        if len(rows) < MIN_NAMES:
            continue
        cs = pd.DataFrame(rows).T
        for h in HORIZONS:
            fwd = cs[f"fwd{h}"].astype(float)
            fr = fwd.rank()
            # 단계1: 단독
            for lab in LB:
                s = cs[lab].astype(float)
                ok = s.notna() & fwd.notna()
                if ok.sum() >= MIN_NAMES:
                    single[h][lab].append(float(s[ok].rank().corr(fr[ok])))
            # 단계2: 블렌드(백분위 가중합)
            pct = {lab: cs[lab].astype(float).rank(pct=True) for lab in LB}
            for name, w in BLENDS.items():
                score = sum(wi * pct[lab] for lab, wi in w.items())
                ok = score.notna() & fwd.notna()
                if ok.sum() >= MIN_NAMES:
                    blend[h][name].append(float(score[ok].rank().corr(fr[ok])))

    for h in HORIZONS:
        print(f"━━━ 미래수익 {h}거래일(≈{h//21}개월) ━━━")
        print("【단계1】 단독 룩백")
        print(f"  {'룩백':<14}{'meanIC':>9}{'IR':>7}{'hit':>8}{'n':>5}")
        for lab in LB:
            m, ir, hit, n = _stats(single[h][lab])
            print(f"  {lab:<14}{m:>9.4f}{ir:>7.2f}{hit:>8.1%}{n:>5}")
        print("【단계2】 가중치 블렌드")
        print(f"  {'구성':<16}{'meanIC':>9}{'IR':>7}{'hit':>8}{'n':>5}")
        for name in BLENDS:
            m, ir, hit, n = _stats(blend[h][name])
            print(f"  {name:<16}{m:>9.4f}{ir:>7.2f}{hit:>8.1%}{n:>5}")
        print()
    print("참고: IC 0.02~0.05면 유의미, IR>0.3이면 안정적. hit는 IC>0인 날 비율.")


if __name__ == "__main__":
    main()
