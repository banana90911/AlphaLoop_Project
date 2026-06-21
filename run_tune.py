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

import copy
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import loader, regime, tune
from config.settings import load_params
from data import cache
from data.sources import index_history
from eval import gate, metrics

RESULTS_DIR = Path("tune_results")   # 워크포워드 실행 결과 보존(재실행 없이 재확인용)
CAPITAL = 10_000_000.0
TRAIN, TEST = 252, 63          # IS 1년 / OOS 1분기(거래일)
MOM_WARMUP = 252               # 12-1 모멘텀 워밍업(첫 유효값=252번째 거래일) → 초반 split 제외
INDEX_START = "20200101"       # 지수는 종목보다 앞당겨 받음(SMA 워밍업 확보)
TREND_DAYS = 200               # 하락장 방어: 지수 SMA200 위면 매수 허용(고정, 손잡이 미증가)

# 확장 grid(2차): 기존 4개 범위 확장(경계값 해소) + 핵심 2개(거래당 위험·보유수) 추가 = 6손잡이.
# 손잡이당 2~3값 → 3×3×2×2×2×2 = 144조합. 데이터(17구간) 한계 내에서 PBO 폭증을 피하는 절충.
GRID = {
    ("entry", "score_min"):      [0.40, 0.50, 0.55],   # 1차에서 0.50(최저)만 선택 → 아래로 확장
    ("entry", "stop_atr_k"):     [2.0, 2.5, 3.0],       # 1차에서 2.5(최고) 다수 → 위로 확장
    ("exits", "tp1_R"):          [1.5, 2.0],            # 1차에서 경계 아님(양쪽 선택)
    ("exits", "trail_k"):        [3.0, 3.5],            # 1차에서 3.0(최고) 다수 → 위로 확장
    ("sizing", "risk_pct_max"):  [0.015, 0.025],        # 신규: 거래당 위험 상한(MDD -52%와 직결)
    ("limits", "max_positions"): [5, 8],                # 신규: 동시 보유 종목 수(집중↔분산)
}
_ABBR = {"score_min": "sm", "stop_atr_k": "sk", "tp1_R": "tp",
         "trail_k": "tk", "risk_pct_max": "rp", "max_positions": "mp"}


def _load_index_close(market: str, end: str):
    """시장 지수 종가(date 인덱스). 캐시 없으면 yfinance로 받아 캐시."""
    name = f"index_{market}"
    df = cache.load(name)
    if df is None:
        df = index_history.fetch_index(market, INDEX_START, end)
        cache.save(name, df)
    return df.set_index("date").sort_index()["close"]


def _split_sharpe(returns: pd.Series) -> float:
    """구간(split) 단위 샤프 = 구간수익 평균 ÷ 표준편차. 표준편차 0이면 0."""
    sd = returns.std(ddof=1)
    return float(returns.mean() / sd) if sd and not np.isnan(sd) else 0.0


def main() -> None:
    base = load_params("risk_params")
    tax = load_params("tax_rates")
    uni = cache.load("universe")
    if uni is None:
        raise SystemExit("universe 캐시 없음 — `python -m data.collect` 먼저 실행")
    codes = uni["code"].tolist()
    markets_map = dict(zip(uni["code"], uni["market"], strict=True))
    prices, markets = loader.load_prices(codes, markets_map)
    all_dates = sorted({d for df in prices.values() for d in df.index})
    print(f"종목 {len(prices)}개 로드, 거래일 {len(all_dates)}일")
    print(f"grid {len(tune.param_grid(base, GRID))}조합 × {len(GRID)}손잡이")

    # ── 하락장 방어: 시장별 지수 추세 ──
    end_ymd = (pd.Timestamp(all_dates[-1]) + pd.Timedelta(days=1)).strftime("%Y%m%d")
    idx_close = {mk: _load_index_close(mk, end_ymd) for mk in set(markets.values())}
    trend = regime.market_trend(idx_close, TREND_DAYS)
    print(f"하락장 방어 ON: 지수 SMA{TREND_DAYS} 추세 필터 ({', '.join(idx_close)})\n")

    # ── 워크포워드 튜닝 ──
    recs = tune.walkforward_tune(
        prices, markets, train_size=TRAIN, test_size=TEST,
        initial_capital=CAPITAL, base_params=base, grid=GRID, tax_params=tax,
        market_trend=trend, warmup=MOM_WARMUP,
    )
    print(f"워크포워드 구간 {len(recs)}개 (IS={TRAIN}일 / OOS={TEST}일)")
    print("─" * 78)
    for i, r in enumerate(recs, 1):
        pstr = " ".join(f"{_ABBR[k]}={r.best_params[s][k]}" for (s, k) in GRID)
        print(f"[{i:2d}] OOS {r.split.test_start}~{r.split.test_end} "
              f"수익 {r.oos_return:+7.2%} IS샤프 {r.is_score:5.2f} │ {pstr}")
    print("─" * 78)

    # ── 전체 OOS 곡선 vs 벤치마크 ──
    oos_eq = tune.oos_equity(recs, initial_capital=CAPITAL)
    if oos_eq.empty or len(oos_eq) < 2:
        print("OOS 거래 부족 — 판정 불가")
        return
    strat = metrics.summary(oos_eq)
    print(f"\n■ 전체 OOS  누적 {strat['total_return']:+.2%}  "
          f"샤프 {strat['sharpe']:.2f}  MDD {strat['max_drawdown']:.2%}  "
          f"Calmar {strat['calmar']:.2f}")

    # 벤치마크 4종(10-1): OOS 기간 net 누적수익으로 비교 — 전부 초과해야 ① 통과
    bench_start = recs[0].split.test_start
    bench_prices = {c: df["close"] for c, df in prices.items()}
    ew = metrics.equal_weight_equity(bench_prices, CAPITAL)
    ew = ew.loc[ew.index >= bench_start]
    # 코스피 지수(없으면 첫 시장). bh·mom은 전 기간 계산 후 OOS 슬라이스(SMA 워밍업 확보)
    kospi = idx_close["KOSPI"] if "KOSPI" in idx_close else next(iter(idx_close.values()))
    bh = metrics.buy_and_hold_equity(kospi, CAPITAL)
    bh = bh.loc[bh.index >= bench_start]
    mom = metrics.momentum_equity(kospi, TREND_DAYS, CAPITAL)
    mom = mom.loc[mom.index >= bench_start]
    bench = {
        "equal_weight": metrics.total_return(ew),
        "kospi_buy_hold": metrics.total_return(bh),
        "momentum": metrics.total_return(mom),
        "cash": 0.0,
    }
    for name, val in bench.items():
        print(f"   벤치 {name:15s} {val:+.2%}")

    # ── 과최적화 검정 ──
    candidates = tune.param_grid(base, GRID)
    mat = tune.perf_matrix(prices, markets, train_size=TRAIN, test_size=TEST,
                           initial_capital=CAPITAL, base_params=base, grid=GRID,
                           tax_params=tax, market_trend=trend, warmup=MOM_WARMUP)
    n_blocks = (mat.shape[0] // 2) * 2          # PBO용 짝수 블록 (≤ 구간수)
    pbo = gate.pbo_cscv(mat, n_splits=n_blocks) if n_blocks >= 2 else float("nan")

    # DSR: 구간(split) 단위로 정합 계산 — observed·sr_std·표본을 같은 단위로.
    # sr_std는 후보군(perf_matrix)의 구간샤프 분포에서 산출(다중비교 보정의 핵심).
    sel = pd.Series([r.oos_return for r in recs])             # 선택전략 구간수익
    observed_sr = _split_sharpe(sel)
    cand_sr = [s for j in range(mat.shape[1])
               if (s := _split_sharpe(pd.Series(mat[:, j]))) != 0.0]
    sr_std = float(np.std(cand_sr, ddof=1)) if len(cand_sr) > 1 else 0.0
    dsr = gate.deflated_sharpe(observed_sr, len(sel), len(candidates), sr_std,
                               skew=float(sel.skew()),
                               kurtosis=float(sel.kurtosis() + 3.0))

    print(f"\n■ 과최적화  PBO {pbo:.2%} (블록 {n_blocks})  "
          f"DSR {dsr:.2%}  후보 {len(candidates)}개 "
          f"(obs_sr {observed_sr:.3f}·sr_std {sr_std:.3f})")

    # ── 모달 추천 파라미터(민감도 기준점) ──
    rec = tune.recommend_params(recs, base, GRID)

    # ── 견고성(10-1 ③) 실측 ──
    # ① 민감도: 추천 파라미터 ±1칸 이웃의 OOS 누적이 급락(절벽)하지 않는지 (mat 재사용, 추가실행 0)
    no_cliff = gate.sensitivity_no_cliff(mat, candidates, GRID, rec)
    # ② 비용 2배 스트레스: 수수료·슬리피지·거래세 2배로 재실행 → 균등가중 벤치 초과 유지?
    stress_tax = copy.deepcopy(tax)
    stress_tax["brokerage"]["rate"] *= 2
    stress_tax["slippage"]["rate"] *= 2
    for row in stress_tax["sell_tax"]:
        row["rate"] *= 2
    stress_recs = tune.walkforward_tune(
        prices, markets, train_size=TRAIN, test_size=TEST,
        initial_capital=CAPITAL, base_params=base, grid=GRID,
        tax_params=stress_tax, market_trend=trend, warmup=MOM_WARMUP,
    )
    stress_eq = tune.oos_equity(stress_recs, initial_capital=CAPITAL)
    stress_ret = metrics.total_return(stress_eq) if len(stress_eq) >= 2 else -1.0
    stress_beats = stress_ret > bench["equal_weight"]
    print(f"\n■ 견고성  민감도절벽없음 {no_cliff}  "
          f"비용2배 OOS {stress_ret:+.2%} (>균등 {bench['equal_weight']:+.2%}? {stress_beats})")

    # ── 방향성 게이트(하드 3축, 누적수익 기준 / DSR은 보조) ──
    g = gate.directional_gate(
        strategy_score=strat["total_return"],
        benchmark_scores=bench,
        pbo=pbo if not np.isnan(pbo) else 1.0,
        sensitivity_no_cliff=no_cliff,
        stress_beats_benchmarks=stress_beats,
        dsr=dsr,
    )
    print(f"\n■ 게이트: {'GO ✅' if g.passed else 'NO-GO ⛔'}  "
          f"(DSR {g.dsr:.2%} → 자본속도 '{g.dsr_tier}', 게이트 축 아님)")
    for k, v in g.checks.items():
        print(f"   {'✓' if v else '✗'} {k}")

    # ── 추천 파라미터(모달) ──
    print("\n■ 추천 파라미터(모달):")
    for (s, k) in GRID:
        print(f"   {s}.{k:14s} {base[s][k]} → {rec[s][k]}")

    # ── 결과 저장(재실행 없이 재확인) ──
    result = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "n_stocks": len(prices),
            "n_trading_days": len(all_dates),
            "period": [str(all_dates[0]), str(all_dates[-1])],
            "n_splits": len(recs),
            "grid_combos": len(candidates),
            "caveat_survivorship_bias": (
                "현재 상장 종목만 — 상폐 종목 미포함. "
                "결과는 낙관(생존편향) 방향으로 부풀려질 수 있음."
            ),
            "caveat_llm": "코드 경로(A안)만, LLM 미사용.",
        },
        "strategy_oos": strat,
        "benchmarks": bench,
        "overfitting": {"pbo": pbo, "dsr": dsr, "dsr_tier": g.dsr_tier},
        "robustness": {
            "sensitivity_no_cliff": no_cliff,
            "stress_2x_oos_return": stress_ret,
            "stress_beats_equal_weight": stress_beats,
        },
        "gate": {"passed": g.passed, "checks": g.checks},
        "recommended_params": {f"{s}.{k}": rec[s][k] for (s, k) in GRID},
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"wf_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    print(f"\n결과 저장: {out_path}")


def _json_default(o):
    """numpy 스칼라를 JSON 직렬화 가능한 파이썬 타입으로."""
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    return str(o)


if __name__ == "__main__":
    main()
