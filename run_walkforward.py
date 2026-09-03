"""
description:        워크포워드 OOS 검증 (학습 구간 선택 → 다음 구간 적용)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import argparse
import copy
import itertools
import json
from datetime import date
from pathlib import Path

import pandas as pd

from backtest import loader, spec_engine as se
from backtest.walkforward import rolling_splits
from config.settings import load_params
from data import cache
from eval import metrics

CAPITAL = 10_000_000.0
WARMUP_START = date(2022, 1, 1)      # 12-1 모멘텀 워밍업 이후부터 분할

# 과최적화 위험이 큰 손잡이만 그리드로
GRID = {
    ("exits", "partial_frac"): [0.0, 0.4],
    ("exits", "trail_k"): [2.75, 4.0],
    ("exits", "max_hold_days"): [20, 9999],
    ("limits", "max_positions"): [10, 20],
}


def _variant(base: dict, combo: tuple) -> dict:
    """base에 combo 조합을 얹은 파라미터 사본을 만든다."""
    p = copy.deepcopy(base)             # load_params는 캐시 공유 → 반드시 사본
    for (sec, key), val in zip(GRID.keys(), combo, strict=True):
        p[sec][key] = val
    return p


def _label(combo: tuple) -> str:
    """조합을 'key=value ...' 문자열로 표시한다."""
    return " ".join(f"{k}={v}" for (_, k), v in zip(GRID.keys(), combo, strict=True))


def main() -> None:
    """CLI 진입점 — 학습 구간마다 그리드 최적값을 골라 다음 구간에 적용, OOS를 잰다."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=int, default=250, help="학습 거래일 수(약 12개월)")
    ap.add_argument("--test", type=int, default=125, help="검증 거래일 수(약 6개월)")
    ap.add_argument("--out", default="tune_results/walkforward_oos.json")
    args = ap.parse_args()

    base, tax = load_params("risk_params"), load_params("tax_rates")
    uni = cache.load("universe")
    if uni is None:
        raise SystemExit("universe 캐시 없음")
    prices, markets = loader.load_prices(
        uni["code"].tolist(), dict(zip(uni["code"], uni["market"], strict=True)))
    feats = {c: se.build_spec_features(df) for c, df in prices.items()}
    all_dates = sorted({d for f in feats.values() for d in f.index})
    dates = [d for d in all_dates if d >= WARMUP_START]
    splits = rolling_splits(dates, train_size=args.train, test_size=args.test)
    combos = list(itertools.product(*GRID.values()))
    print(f"로드 {len(prices)}종목 · 분할 {len(splits)}개 · 그리드 {len(combos)}조합")
    print(f"학습 {args.train}일 → 검증 {args.test}일\n")

    def run(params, start, end, schedule=None):
        return se.run(prices, markets, start=start, end=end, initial_capital=CAPITAL,
                      entry_timing="last", params=params, tax_params=tax, feats=feats,
                      params_schedule=schedule)

    # ── 각 학습 구간에서 최적 조합 선택 → 다음 검증 구간에 적용할 스케줄 구성 ──
    schedule, picks = [], []
    for i, sp in enumerate(splits, 1):
        best, best_score = None, -1e18
        for combo in combos:
            r = run(_variant(base, combo), sp.train_start, sp.train_end)
            if r.equity.empty or len(r.equity) < 2:
                continue
            m = metrics.summary(r.equity)
            score = m["sharpe"]                     # 학습 목적함수 = 샤프
            if score > best_score:
                best, best_score = combo, score
        if best is None:
            continue
        schedule.append((sp.test_start, _variant(base, best)))
        picks.append({"test_start": sp.test_start.isoformat(),
                      "test_end": sp.test_end.isoformat(),
                      "picked": _label(best), "train_sharpe": round(best_score, 2)})
        print(f"[{i}/{len(splits)}] 학습 {sp.train_start}~{sp.train_end} → "
              f"선택 {_label(best)} (샤프 {best_score:.2f}) → 검증 {sp.test_start}~{sp.test_end}")

    oos_start, oos_end = splits[0].test_start, splits[-1].test_end
    print(f"\nOOS 구간 {oos_start} ~ {oos_end}")

    aggressive = copy.deepcopy(base)
    aggressive["exits"].update(partial_frac=0.0, trail_k=4.0, max_hold_days=9999)
    aggressive["entry"]["stop_atr_k"] = 3.0

    runs = {
        "wf_tuned": ("① WF 튜닝(구간마다 재선택)", run(base, oos_start, oos_end, schedule)),
        "fixed_base": ("② 고정 기본값(현재 확정)", run(base, oos_start, oos_end)),
        "fixed_aggr": ("③ 고정 공격형", run(aggressive, oos_start, oos_end)),
    }

    kospi = cache.load("index_KOSPI").set_index("date").sort_index()["close"]
    kospi = kospi[(kospi.index >= oos_start) & (kospi.index <= oos_end)]

    print(f"\n{'':<28}{'누적':>10}{'샤프':>8}{'MDD':>9}{'거래':>7}")
    out = {"oos_start": oos_start.isoformat(), "oos_end": oos_end.isoformat(),
           "train_days": args.train, "test_days": args.test, "picks": picks, "runs": {}}
    for key, (label, r) in runs.items():
        m = metrics.summary(r.equity)
        print(f"{label:<28}{m['total_return']:>10.1%}{m['sharpe']:>8.2f}"
              f"{m['max_drawdown']:>9.1%}{len(r.trades):>7}")
        out["runs"][key] = {"label": label, "total_return": m["total_return"],
                            "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"],
                            "trades": len(r.trades), "diag": r.diag}
    if not kospi.empty:
        m = metrics.summary(kospi)
        print(f"{'코스피 매수후보유':<28}{m['total_return']:>10.1%}{m['sharpe']:>8.2f}"
              f"{m['max_drawdown']:>9.1%}")
        out["benchmark_kospi"] = {"total_return": m["total_return"], "sharpe": m["sharpe"],
                                  "max_drawdown": m["max_drawdown"]}

    p = Path(args.out)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장 {p}")


if __name__ == "__main__":
    main()
