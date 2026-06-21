"""모멘텀 룩백×스킵 그리드 IC (탐색용). momentum_ic.py의 확장.

질문: 어느 *룩백*(추세를 얼마나 멀리 보나)과 어느 *스킵*(최근 얼마를 잘라내나)이
한국 시장에서 미래수익을 잘 맞히나? 짧은 기간은 반전(−), 중장기는 추세(+)일 거란
가설을 실측한다.

각 칸 = mean Rank IC. +면 추세 지속(모멘텀), −면 반전. 단일 룩백 기준.
사용: .venv/bin/python -m analysis.momentum_grid
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import loader
from data import cache

REBAL = 21
MIN_NAMES = 30
HORIZONS = [21, 63]                       # 미래수익(1·3개월)
LOOKBACKS = [10, 20, 40, 63, 126, 189, 252]   # 2주·1·2·3·6·9·12개월
SKIPS = [0, 10, 20]                       # 스킵 0·2주·1개월
MIN_WINDOW = 20                           # 룩백−스킵이 이 미만이면 의미없어 제외


def main() -> None:
    uni = cache.load("universe")
    prices, _ = loader.load_prices(uni["code"].tolist())
    print(f"종목 {len(prices)}개 로드\n")

    combos = [(lb, sk) for lb in LOOKBACKS for sk in SKIPS if lb - sk >= MIN_WINDOW]

    # 종목별: 각 (lb,sk) 모멘텀 + 각 horizon 미래수익을 한 프레임에
    feats: dict[str, pd.DataFrame] = {}
    for code, df in prices.items():
        if df.empty or "close" not in df:
            continue
        c = df["close"]
        v = pd.DataFrame(index=c.index)
        for lb, sk in combos:
            v[f"m_{lb}_{sk}"] = c.shift(sk) / c.shift(lb) - 1.0
        for h in HORIZONS:
            v[f"fwd{h}"] = c.shift(-h) / c - 1.0
        feats[code] = v

    all_dates = sorted({d for v in feats.values() for d in v.index})
    sample = all_dates[::REBAL]

    # IC 누적: ic[h][(lb,sk)] = 리스트
    ic = {h: {cb: [] for cb in combos} for h in HORIZONS}
    for d in sample:
        rows = {code: v.loc[d] for code, v in feats.items() if d in v.index}
        if len(rows) < MIN_NAMES:
            continue
        cs = pd.DataFrame(rows).T
        for h in HORIZONS:
            fwd = cs[f"fwd{h}"].astype(float)
            fr = fwd.rank()
            for lb, sk in combos:
                s = cs[f"m_{lb}_{sk}"].astype(float)
                ok = s.notna() & fwd.notna()
                if ok.sum() >= MIN_NAMES:
                    ic[h][(lb, sk)].append(float(s[ok].rank().corr(fr[ok])))

    mon = {10: "2주", 20: "1개월", 40: "2개월", 63: "3개월",
           126: "6개월", 189: "9개월", 252: "12개월"}
    for h in HORIZONS:
        print(f"=== 미래수익 {h}거래일(≈{h//21}개월) │ mean IC (+추세 / −반전) ===")
        hdr = "룩백\\스킵".ljust(10) + "".join(f"{('스킵'+str(s)+'일') if s else '스킵없음':>11}" for s in SKIPS)
        print(hdr)
        print("─" * len(hdr))
        for lb in LOOKBACKS:
            line = f"{mon[lb]:<8}"
            for sk in SKIPS:
                cb = (lb, sk)
                if cb not in ic[h] or not ic[h][cb]:
                    line += f"{'·':>11}"
                else:
                    arr = np.array(ic[h][cb])
                    line += f"{arr.mean():>11.4f}"
            print(line)
        print()
    print("참고: IC 0.02~0.05면 유의미. 스킵N일=최근 N거래일을 모멘텀에서 제외.")


if __name__ == "__main__":
    main()
