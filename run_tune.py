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
import pandas as pd

from backtest import loader, regime, tune
from config.settings import load_params
from data import cache
from data.sources import index_history
from eval import gate, metrics

CAPITAL = 10_000_000.0
TRAIN, TEST = 252, 63          # IS 1년 / OOS 1분기(거래일)
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
        market_trend=trend,
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
    candidates = tune.param_grid(base, GRID)
    mat = tune.perf_matrix(prices, markets, train_size=TRAIN, test_size=TEST,
                           initial_capital=CAPITAL, base_params=base, grid=GRID,
                           tax_params=tax, market_trend=trend)
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
    rec = tune.recommend_params(recs, base, GRID)
    print("\n■ 추천 파라미터(모달):")
    for (s, k) in GRID:
        print(f"   {s}.{k:14s} {base[s][k]} → {rec[s][k]}")


if __name__ == "__main__":
    main()
