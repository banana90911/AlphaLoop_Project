"""워크포워드 OOS 검증 (09-eval 9.1).

**무엇을 왜 검증하나** — 지금까지 파라미터를 2023~2026 전 구간 성적을 *보면서* 골랐다.
그러면 "전략이 좋은 것"과 "그 기간에만 우연히 맞는 값을 찾은 것"이 구분되지 않는다.
그래서 학습 구간에서만 값을 고르고, 그 값을 *고를 때 보지 않은* 다음 구간(OOS)에 적용해
성적을 잰다. 검증 결과를 보고 값을 되돌려 고치지 않는다.

**어떻게** — `backtest/walkforward.rolling_splits`로 학습/검증을 시간순 분할하고, 각 학습
구간의 그리드 최적값을 다음 검증 구간에 적용한다. 검증 구간들의 파라미터를 하나의
스케줄로 묶어 **자본·보유를 이어가며 한 번에** 실행한다 — 구간마다 자본을 초기화하는
콜드스타트가 장기보유 모멘텀을 토막내는 계측 결함(9.6.5)을 피하기 위함이다.

비교 대상 셋을 같은 OOS 기간에서 나란히 본다.
  ① WF 튜닝   — 구간마다 학습 최적값을 갈아끼움
  ② 고정 기본값 — 현재 확정값(config 그대로)
  ③ 고정 공격형 — 부분익절 0·트레일 4.0·시간청산 없음·손절 3.0 ATR

사용: PYTHONPATH=. .venv/bin/python run_walkforward.py [--train 250] [--test 125]
"""
from __future__ import annotations

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
WARMUP_START = date(2022, 1, 1)      # 12-1 모멘텀 워밍업(252+20거래일) 이후부터 분할

# 과최적화 위험이 큰 손잡이만 그리드로 (09-eval 9.3: 손잡이 7개 이하)
GRID = {
    ("exits", "partial_frac"): [0.0, 0.4],
    ("exits", "trail_k"): [2.75, 4.0],
    ("exits", "max_hold_days"): [20, 9999],
    ("limits", "max_positions"): [10, 20],
}


def _variant(base: dict, combo: tuple) -> dict:
    p = copy.deepcopy(base)             # load_params는 캐시 공유 → 반드시 사본
    for (sec, key), val in zip(GRID.keys(), combo, strict=True):
        p[sec][key] = val
    return p


def _label(combo: tuple) -> str:
    return " ".join(f"{k}={v}" for (_, k), v in zip(GRID.keys(), combo, strict=True))


def main() -> None:
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
