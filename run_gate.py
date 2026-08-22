"""Go/No-Go 게이트 실행기 — 동결 기본값 연속 백테스트 1회 (09-eval 9.2·9.6.5).

그리드 튜닝을 기각했으므로(9.6.5) 파라미터를 고르지 않는다.
`config/risk_params.toml` 기본값을 그대로 검증기간에 **연속 보유**로 1회 실행하고 하드
3조건으로 판정한다.

  ① 벤치마크 4종 전부 초과 — 코스피 매수후보유·단순 모멘텀·현금·균등가중 워치리스트
  ② PBO — 그리드를 탐색하지 않으므로 판정 대상 아님(9.6.5)
  ③ 견고성 — 파라미터 ±20% 재실행이 기준선(기본값의 50%, `drop_tol=0.5`) 위 + 비용 2배에서
     균등가중 초과

엔진은 설계 정합 정본(`backtest/spec_engine`)을 쓴다 — 구 `engine.py`는 설계 위반 14건으로
폐기 대상이다(9.6.3). 하락장 필터(regime)는 설계에 없으므로 주입하지 않는다(04-data 지수 행).

사용: PYTHONPATH=. .venv/bin/python run_gate.py [--partial 0.4]

`--partial FRAC`은 +breakeven_R 도달 시 FRAC만큼 부분 청산하는 변형을 돌린다 — 부분 익절은
설계에서 제거했으므로(06-sizing 6.2) **비교 측정 전용**이다.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import date, datetime
from pathlib import Path

from backtest import loader, spec_engine as engine
from config.settings import load_params
from data import cache
from eval import metrics

RESULTS_DIR = Path("tune_results")
CAPITAL = 10_000_000.0
GATE_START = date(2023, 1, 1)     # 검증 시작(9.6.4~9.6.5 실행과 동일 구간)
TREND_DAYS = 200                  # 벤치마크용 단순 모멘텀 지수 SMA (전략 입력 아님)
DROP_TOL = 0.5                    # 견고성: 기본값 대비 이 비율 아래로 꺾이면 절벽
SENSITIVITY = 0.20                # ±20%

# ±20%를 흔들 파라미터 — 조정 가능한 손잡이 목록(9.3: 7개 이하로 고정)
KNOBS: list[tuple[str, str, str]] = [
    ("entry", "stop_atr_k", "float"),
    ("exits", "breakeven_R", "float"),
    ("exits", "trail_k", "float"),
    ("exits", "max_hold_days", "int"),
    ("limits", "max_positions", "int"),
]


def _shift(params: dict, section: str, key: str, kind: str, factor: float) -> dict | None:
    """파라미터 하나를 factor배로 흔든 사본. 값이 안 바뀌면 None(정수 반올림 등)."""
    out = copy.deepcopy(params)
    base_val = params[section][key]
    new = base_val * factor
    new = max(1, round(new)) if kind == "int" else round(new, 4)
    if new == base_val:
        return None
    out[section][key] = new
    return out


def _double_costs(tax: dict) -> dict:
    """수수료·슬리피지·거래세 2배 (비용 2배 스트레스)."""
    out = copy.deepcopy(tax)
    out["brokerage"]["rate"] *= 2
    out["slippage"]["rate"] *= 2
    for row in out["sell_tax"]:
        row["rate"] *= 2
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partial", type=float, default=0.0,
                    help="부분 익절 비율(0=없음, 설계 기본). 비교 측정 전용")
    args = ap.parse_args()

    base = load_params("risk_params")
    tax = load_params("tax_rates")
    if args.partial > 0:
        base = copy.deepcopy(base)          # load_params는 캐시 공유 → 반드시 사본
        base["exits"]["partial_frac"] = args.partial
    uni = cache.load("universe")
    if uni is None:
        raise SystemExit("universe 캐시 없음 — `python -m data.collect` 먼저 실행")

    codes = uni["code"].tolist()
    markets_map = dict(zip(uni["code"], uni["market"], strict=True))
    prices, markets = loader.load_prices(codes, markets_map)
    feats = {c: engine.build_spec_features(df) for c, df in prices.items()}
    all_dates = sorted({d for df in prices.values() for d in df.index})
    end = all_dates[-1]

    idx_close = {}
    for mk in set(markets.values()):
        df = cache.load(f"index_{mk}")
        if df is not None:
            idx_close[mk] = df.set_index("date").sort_index()["close"]

    frac = float(base["exits"].get("partial_frac", 0.0))
    variant = f"부분익절 {frac:.0%}" if frac > 0 else "부분익절 없음"
    print(f"종목 {len(prices)}개 · 검증 {GATE_START}~{end} · 자본 {CAPITAL:,.0f}원 · {variant}")
    print(f"동결 기본값: {', '.join(f'{k}={base[s][k]}' for s, k, _ in KNOBS)}\n")

    def run(params: dict, tax_params: dict) -> engine.BacktestResult:
        return engine.run(prices, markets, start=GATE_START, end=end,
                          initial_capital=CAPITAL, params=params,
                          tax_params=tax_params, feats=feats, entry_timing="last")

    # ── 기본값 연속 실행 ──
    res = run(base, tax)
    if res.equity.empty:
        raise SystemExit("거래 없음 — 판정 불가")
    strat = metrics.summary(res.equity)
    base_ret = strat["total_return"]
    print(f"■ 전략  누적 {base_ret:+.2%}  샤프 {strat['sharpe']:.2f}  "
          f"MDD {strat['max_drawdown']:.2%}  거래 {len(res.trades)}건")

    # ── ① 벤치마크 4종 (09-eval 9.1 정의) ──
    bench_prices = {c: df["close"] for c, df in prices.items()}
    ew = metrics.equal_weight_equity(bench_prices, CAPITAL)
    kospi = idx_close["KOSPI"] if "KOSPI" in idx_close else next(iter(idx_close.values()))
    bh = metrics.buy_and_hold_equity(kospi, CAPITAL)
    mom = metrics.momentum_equity(kospi, TREND_DAYS, CAPITAL)
    bench = {
        "kospi_buy_hold": metrics.total_return(bh.loc[bh.index >= GATE_START]),
        "momentum": metrics.total_return(mom.loc[mom.index >= GATE_START]),
        "cash": 0.0,
        "equal_weight": metrics.total_return(ew.loc[ew.index >= GATE_START]),
    }
    beats_all = all(base_ret > v for v in bench.values())
    print("\n■ ① 벤치마크")
    for name, val in bench.items():
        mark = "초과" if base_ret > val else "미달"
        print(f"   {name:16s} {val:+8.2%}  → {mark}")
    print(f"   판정: {'통과' if beats_all else '미달'}")

    # ── ③ 견고성: 파라미터 ±20% + 비용 2배 ──
    floor = base_ret * (1.0 - DROP_TOL)
    print(f"\n■ ③ 견고성 (기준선 = 기본값의 {1 - DROP_TOL:.0%} = {floor:+.2%})")
    sens: list[dict] = []
    for section, key, kind in KNOBS:
        for factor in (1 - SENSITIVITY, 1 + SENSITIVITY):
            p = _shift(base, section, key, kind, factor)
            if p is None:
                continue
            r = metrics.total_return(run(p, tax).equity)
            ok = r >= floor
            sens.append({"param": f"{section}.{key}", "value": p[section][key],
                         "total_return": r, "pass": ok})
            print(f"   {key:16s} {base[section][key]:>7} → {p[section][key]:<7} "
                  f"{r:+8.2%}  {'통과' if ok else '절벽'}")
    no_cliff = all(s["pass"] for s in sens)

    stress_ret = metrics.total_return(run(base, _double_costs(tax)).equity)
    stress_ok = stress_ret > bench["equal_weight"]
    print(f"   비용 2배         {stress_ret:+8.2%}  "
          f"(>균등 {bench['equal_weight']:+.2%}? {'통과' if stress_ok else '미달'})")
    robust = no_cliff and stress_ok
    print(f"   판정: {'통과' if robust else '미달'}")

    verdict = "GO" if (beats_all and robust) else "NO-GO"
    print(f"\n{'=' * 60}\n게이트: {verdict}  "
          f"(① 벤치 {'✅' if beats_all else '⛔'} · ② PBO 판정대상 아님 · "
          f"③ 견고성 {'✅' if robust else '⛔'})\n{'=' * 60}")

    RESULTS_DIR.mkdir(exist_ok=True)
    tag = f"_p{int(args.partial * 100)}" if args.partial > 0 else ""
    out = RESULTS_DIR / f"gate_{datetime.now().strftime('%Y%m%d_%H%M%S')}{tag}.json"
    out.write_text(json.dumps({
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "period": [str(GATE_START), str(end)],
        "universe": len(prices),
        "capital": CAPITAL,
        "variant": variant,
        "params": {f"{s}.{k}": base[s][k] for s, k, _ in KNOBS},
        "strategy": {**{k: float(v) for k, v in strat.items()},
                     "trades": len(res.trades)},
        "benchmarks": bench,
        "sensitivity": sens,
        "stress_double_cost": stress_ret,
        "gate": {"benchmarks": beats_all, "robustness": robust, "verdict": verdict},
    }, ensure_ascii=False, indent=2))
    print(f"결과 저장: {out}")


if __name__ == "__main__":
    main()
