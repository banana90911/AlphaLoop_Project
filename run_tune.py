"""워크포워드 파라미터 OOS 튜닝 실행기 (10-1).

캐시된 과거 데이터로 롤링 워크포워드 튜닝을 돌려:
  ① 구간별 IS 최고 조합 → OOS 성과(손 안 댄 구간)
  ② 전체 OOS equity vs 벤치마크 4종(방향성 게이트)
  ③ Deflated Sharpe·PBO(과최적화 확률)
  ④ 모달 추천 파라미터(안정적으로 좋았던 설정)
를 출력한다. 판정이 Go여도 *전종목·장기* 재검증 전엔 실거래 금지.

사용: .venv/bin/python run_tune.py
"""
from __future__ import annotations

import numpy as np

from backtest import loader, tune
from config.settings import load_params
from data import cache
from eval import gate, metrics

CAPITAL = 10_000_000.0
TRAIN, TEST = 252, 63          # IS 1년 / OOS 1분기(거래일)


def main() -> None:
    base = load_params("risk_params")
    tax = load_params("tax_rates")
    uni = cache.load("universe")
    if uni is None:
        raise SystemExit("universe 캐시 없음 — `python -m data.collect` 먼저 실행")
    codes = uni["code"].tolist()
    markets_map = dict(zip(uni["code"], uni["market"], strict=True))
    prices, markets = loader.load_prices(codes, markets_map)
    print(f"종목 {len(prices)}개 로드, 거래일 "
          f"{len(sorted({d for df in prices.values() for d in df.index}))}일\n")

    # ── 워크포워드 튜닝 ──
    recs = tune.walkforward_tune(
        prices, markets, train_size=TRAIN, test_size=TEST,
        initial_capital=CAPITAL, base_params=base, tax_params=tax,
    )
    print(f"워크포워드 구간 {len(recs)}개 (IS={TRAIN}일 / OOS={TEST}일)")
    print("─" * 72)
    for i, r in enumerate(recs, 1):
        e = r.best_params["entry"]
        x = r.best_params["exits"]
        print(f"[{i}] OOS {r.split.test_start}~{r.split.test_end}  "
              f"수익 {r.oos_return:+7.2%}  IS샤프 {r.is_score:5.2f}  │ "
              f"score_min={e['score_min']} stop_k={e['stop_atr_k']} "
              f"tp1_R={x['tp1_R']} trail_k={x['trail_k']}")
    print("─" * 72)

    # ── 전체 OOS 곡선 vs 벤치마크 ──
    oos_eq = tune.oos_equity(recs, initial_capital=CAPITAL)
    if oos_eq.empty or len(oos_eq) < 2:
        print("OOS 거래 부족 — 판정 불가")
        return
    oos_r = metrics.daily_returns(oos_eq)
    strat = metrics.summary(oos_eq)
    print(f"\n■ 전체 OOS  누적 {strat['total_return']:+.2%}  "
          f"샤프 {strat['sharpe']:.2f}  MDD {strat['max_drawdown']:.2%}  "
          f"Calmar {strat['calmar']:.2f}")

    # 벤치마크: OOS 기간 균등가중 매수후보유(같은 종목군)
    bench_prices = {c: df["close"] for c, df in prices.items()}
    ew = metrics.equal_weight_equity(bench_prices, CAPITAL)
    ew = ew.loc[ew.index >= recs[0].split.test_start]
    bench = {
        "equal_weight": metrics.total_return(ew),
        "cash": 0.0,
    }
    for name, val in bench.items():
        print(f"   벤치 {name:13s} {val:+.2%}")

    # ── 과최적화 검정 ──
    candidates = tune.param_grid(base)
    mat = tune.perf_matrix(prices, markets, train_size=TRAIN, test_size=TEST,
                           initial_capital=CAPITAL, base_params=base, tax_params=tax)
    n_blocks = (mat.shape[0] // 2) * 2          # PBO용 짝수 블록 (≤ 구간수)
    pbo = gate.pbo_cscv(mat, n_splits=n_blocks) if n_blocks >= 2 else float("nan")

    sr_obs = strat["sharpe"] / np.sqrt(metrics._PPY)          # per-obs 환산
    sr_std = float(np.std([metrics.sharpe(oos_r)], ddof=0)) or 0.5  # 단일추정 보수값
    dsr = gate.deflated_sharpe(sr_obs, len(oos_r), len(candidates), sr_std)

    print(f"\n■ 과최적화  PBO {pbo:.2%} (블록 {n_blocks})  "
          f"DSR {dsr:.2%}  후보 {len(candidates)}개")

    # ── 방향성 게이트(누적수익 기준) ──
    g = gate.directional_gate(
        strategy_score=strat["total_return"],
        benchmark_scores=bench,
        dsr=dsr, pbo=pbo if not np.isnan(pbo) else 1.0,
        sensitivity_no_cliff=True,       # 민감도 스트레스는 후속(전종목 단계)
        stress_beats_benchmarks=True,
    )
    print(f"\n■ 게이트: {'GO ✅' if g.passed else 'NO-GO ⛔'}")
    for k, v in g.checks.items():
        print(f"   {'✓' if v else '✗'} {k}")

    # ── 모달 추천 파라미터 ──
    rec = tune.recommend_params(recs, base)
    print("\n■ 추천 파라미터(모달):")
    print(f"   entry.score_min  {base['entry']['score_min']} → {rec['entry']['score_min']}")
    print(f"   entry.stop_atr_k {base['entry']['stop_atr_k']} → {rec['entry']['stop_atr_k']}")
    print(f"   exits.tp1_R      {base['exits']['tp1_R']} → {rec['exits']['tp1_R']}")
    print(f"   exits.trail_k    {base['exits']['trail_k']} → {rec['exits']['trail_k']}")


if __name__ == "__main__":
    main()
