"""모멘텀 기간 정의 비교 — Rank IC 분석 (탐색용, 정식 산출물 아님).

목적: "현재 60일 / 개선A(120-20스킵) / 개선B(멀티룩백 고정가중)" 세 모멘텀 정의 중
한국 시장(KOSPI/KOSDAQ 캐시)에서 어느 것이 미래수익을 더 잘 맞히는지 본다.

Rank IC = 어떤 거래일의 종목별 모멘텀 *순위*가 H거래일 뒤 실제 수익률 *순위*와
얼마나 일치하는가(Spearman 상관). +면 모멘텀이 미래수익과 같은 방향(추세 지속),
−면 반전. 0이면 무관. 전체 백테스트와 달리 스크리너·청산·사이징을 배제해
모멘텀 신호 *자체*의 예측력만 분리한다.

지표:
  mean IC : 평균 IC(예측력의 부호·크기). 주식 횡단면에선 0.02~0.05만 돼도 의미 있음.
  IC IR   : mean / std (안정성, 정보비율). 높을수록 들쭉날쭉하지 않음.
  hit     : IC>0 비율(맞힌 날 비중).

사용: .venv/bin/python -m analysis.momentum_ic
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import loader
from data import cache

REBAL = 21       # 횡단면 표본 간격(거래일, ≈1개월)
HORIZON = 21     # 미래수익 측정 구간(거래일, ≈1개월)
MIN_NAMES = 30   # 한 거래일에 유효 종목 이 미만이면 그 날 표본 제외
MIN_VALUE = None # 유동성 필터(일평균 거래대금) — None이면 미적용

# 개선B 멀티룩백 고정 가중치(최근 가중, 합 1.0). 각 룩백 끝점은 20일 전(반전 스킵).
B_WEIGHTS = {63: 0.5, 126: 0.3, 252: 0.2}   # 3·6·12개월
SKIP = 20


def variants(close: pd.Series) -> pd.DataFrame:
    """종목 종가 → 세 모멘텀 변형 시계열(같은 인덱스)."""
    out = pd.DataFrame(index=close.index)
    # 현재: 오늘 ÷ 60일 전
    out["cur60"] = close / close.shift(60) - 1.0
    # 개선A: 20일 전 ÷ 120일 전 (본진 구간 + 최근 1개월 스킵)
    out["a_120_20"] = close.shift(SKIP) / close.shift(120) - 1.0
    # 개선B: 멀티룩백 각각 (가중합은 횡단면에서 순위 기준으로 합산)
    for lb in B_WEIGHTS:
        out[f"lb{lb}"] = close.shift(SKIP) / close.shift(lb) - 1.0
    return out


def main() -> None:
    uni = cache.load("universe")
    if uni is None:
        raise SystemExit("universe 캐시 없음 — `python -m data.collect` 먼저")
    codes = uni["code"].tolist()
    prices, _ = loader.load_prices(codes)
    print(f"종목 {len(prices)}개 로드")

    # 종목별: 변형 + 미래수익(H일) 을 long 테이블로 모은다
    feats: dict[str, pd.DataFrame] = {}
    for code, df in prices.items():
        if df.empty or "close" not in df:
            continue
        c = df["close"]
        v = variants(c)
        v["fwd"] = c.shift(-HORIZON) / c - 1.0          # 미래 H일 수익률
        if MIN_VALUE is not None and "volume" in df:
            v["vt"] = (c * df["volume"]).rolling(20).mean()
        feats[code] = v

    # 전체 거래일 → REBAL 간격으로 표본 날짜 선정
    all_dates = sorted({d for v in feats.values() for d in v.index})
    sample_dates = all_dates[::REBAL]

    var_cols = ["cur60", "a_120_20"] + [f"lb{lb}" for lb in B_WEIGHTS]
    ics: dict[str, list[float]] = {"cur60": [], "a_120_20": [], "multiB": []}

    for d in sample_dates:
        rows = {}
        for code, v in feats.items():
            if d not in v.index:
                continue
            r = v.loc[d]
            if pd.isna(r["fwd"]):
                continue
            if MIN_VALUE is not None and ("vt" not in r or pd.isna(r["vt"]) or r["vt"] < MIN_VALUE):
                continue
            rows[code] = r
        if len(rows) < MIN_NAMES:
            continue
        cs = pd.DataFrame(rows).T
        fwd = cs["fwd"].astype(float)

        # 단일 변형: 값 자체의 Spearman(=순위상관)
        for name in ("cur60", "a_120_20"):
            s = cs[name].astype(float)
            ok = s.notna() & fwd.notna()
            if ok.sum() >= MIN_NAMES:
                ics[name].append(_spearman(s[ok], fwd[ok]))

        # 멀티B: 각 룩백을 횡단면 백분위로 바꿔 고정가중 합 → 그 합의 Spearman
        score = pd.Series(0.0, index=cs.index)
        wsum = 0.0
        for lb, w in B_WEIGHTS.items():
            s = cs[f"lb{lb}"].astype(float)
            pct = s.rank(pct=True)        # 결측은 NaN 유지
            score = score.add(w * pct, fill_value=0.0)
            wsum += w
        score = score / wsum
        ok = score.notna() & fwd.notna()
        if ok.sum() >= MIN_NAMES:
            ics["multiB"].append(_spearman(score[ok], fwd[ok]))

    print(f"표본 거래일 {len([d for d in sample_dates])}개 중 유효 측정 다수\n")
    print(f"{'변형':<24}{'mean IC':>10}{'IC IR':>9}{'hit':>8}{'n':>6}")
    print("─" * 57)
    labels = {
        "cur60": "현재 (오늘÷60일전)",
        "a_120_20": "개선A (20일전÷120일전)",
        "multiB": "개선B (멀티 3·6·12개월)",
    }
    for key in ("cur60", "a_120_20", "multiB"):
        arr = np.array(ics[key], dtype=float)
        arr = arr[~np.isnan(arr)]
        if len(arr) == 0:
            print(f"{labels[key]:<24}{'표본없음':>10}")
            continue
        mean = arr.mean()
        ir = mean / arr.std(ddof=1) if arr.std(ddof=1) else float("nan")
        hit = (arr > 0).mean()
        print(f"{labels[key]:<22}{mean:>10.4f}{ir:>9.2f}{hit:>8.1%}{len(arr):>6}")
    print("\n참고: 횡단면 IC는 0.02~0.05면 유의미, IC IR>0.3이면 꽤 안정적.")
    print(f"설정: REBAL={REBAL}일 간격, 미래수익 HORIZON={HORIZON}일, 최소종목 {MIN_NAMES}")


def _spearman(a: pd.Series, b: pd.Series) -> float:
    return float(a.rank().corr(b.rank()))


if __name__ == "__main__":
    main()
