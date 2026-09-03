"""
description:        정기 사이클 시각 비교 (10:00 시가 vs 14:30 종가)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import argparse
import json
from datetime import date
from pathlib import Path

from backtest import loader, spec_engine
from config.settings import load_params
from data import cache
from eval import metrics

CAPITAL = 10_000_000.0
DEFAULT_START = date(2023, 1, 1)


def _benchmark(start: date, end: date) -> dict:
    """코스피 매수후보유 벤치마크 지표를 계산한다."""
    df = cache.load("index_KOSPI")
    if df is None:
        return {}
    s = df.set_index("date").sort_index()["close"]
    s = s[(s.index >= start) & (s.index <= end)]
    if s.empty:
        return {}
    m = metrics.summary(s)
    return {"total_return": m["total_return"], "sharpe": m["sharpe"],
            "max_drawdown": m["max_drawdown"]}


def main() -> None:
    """CLI 진입점 — 두 사이클 시각(첫/마지막)을 같은 구간으로 돌려 비교한다."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="앞 N종목만(소규모 검증)")
    ap.add_argument("--start", default=DEFAULT_START.isoformat())
    ap.add_argument("--out", default="tune_results/cycle_timing.json")
    args = ap.parse_args()
    start = date.fromisoformat(args.start)

    rp, tax = load_params("risk_params"), load_params("tax_rates")
    uni = cache.load("universe")
    if uni is None:
        raise SystemExit("universe 캐시 없음 — `python -m data.collect` 먼저")
    codes = uni["code"].tolist()
    if args.limit:
        codes = codes[: args.limit]
    markets_map = dict(zip(uni["code"], uni["market"], strict=True))
    prices, markets = loader.load_prices(codes, markets_map)
    print(f"로드 {len(prices)}종목")

    feats = {c: spec_engine.build_spec_features(df) for c, df in prices.items()}
    all_dates = sorted({d for f in feats.values() for d in f.index})
    end = all_dates[-1]
    print(f"구간 {start} ~ {end}\n")

    out = {"start": start.isoformat(), "end": end.isoformat(),
           "codes": len(prices), "capital": CAPITAL, "runs": {}}

    for timing, label in (("first", "첫 사이클 10:00(시가)"), ("last", "마지막 사이클 14:30(종가)")):
        r = spec_engine.run(prices, markets, start=start, end=end,
                            initial_capital=CAPITAL, entry_timing=timing,
                            params=rp, tax_params=tax, feats=feats)
        if r.equity.empty:
            print(f"■ {label}: 거래일 없음")
            continue
        m = metrics.summary(r.equity)
        wins = [t for t in r.trades if t.net_pnl > 0]
        wr = len(wins) / len(r.trades) if r.trades else 0.0
        print(f"■ {label}")
        print(f"   누적 {m['total_return']:+.2%}  샤프 {m['sharpe']:.2f}  "
              f"MDD {m['max_drawdown']:.2%}  거래 {len(r.trades)}건  승률 {wr:.1%}")
        print(f"   진단 {r.diag}\n")
        out["runs"][timing] = {
            "label": label, "total_return": m["total_return"], "sharpe": m["sharpe"],
            "max_drawdown": m["max_drawdown"], "trades": len(r.trades),
            "win_rate": wr, "diag": r.diag,
        }

    bm = _benchmark(start, end)
    if bm:
        print(f"■ 코스피 매수후보유  누적 {bm['total_return']:+.2%}  "
              f"샤프 {bm['sharpe']:.2f}  MDD {bm['max_drawdown']:.2%}")
        out["benchmark_kospi"] = bm

    p = Path(args.out)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장 {p}")


if __name__ == "__main__":
    main()
