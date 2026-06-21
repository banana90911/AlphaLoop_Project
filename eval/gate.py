"""Go/No-Go 게이트 — 과최적화 검정 + 방향성 게이트 (eval/gate, 10-1 P1-5).

개인 퀀트가 돈을 잃는 가장 흔한 경로 = 검증을 자기합리화로 건너뛰고 실거래로 가는 것.
그래서 자금과 무관한 **방향성(상대) 게이트**를 코드로 고정한다. **하드 3조건** 중
하나라도 미달이면 절대 임계와 무관하게 No-Go:
  ① 4종 벤치마크를 전부 초과
  ② PBO(Probability of Backtest Overfitting) < 50%
  ③ 파라미터 ±20% 민감도에 절벽 없음 + 거래비용 2배 스트레스에서도 벤치마크 초과

**Deflated Sharpe(DSR)는 하드 게이트에서 제외(2026-06-21 확정, 10-4.3)** — 0.95는
헤지펀드급이라 개인·5년 데이터엔 과도, ">0"은 무력이라 둘 다 극단이었다. DSR은 통과/불통
기준이 아니라 (a) 상시 보고 보조지표, (b) 소액 실전 자본 램프업 신뢰도 입력으로만 쓴다
(`dsr_confidence_tier`). 과최적화 방어는 PBO·견고성이, 운/실력 최종판정은 Phase 7.5 실측이.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
from scipy.stats import norm

_EULER = 0.5772156649015329


def expected_max_sharpe(n_trials: int, sr_std: float) -> float:
    """N회 독립 시도에서 *우연히* 기대되는 최대 Sharpe (Bailey & López de Prado)."""
    if n_trials < 2 or sr_std <= 0:
        return 0.0
    z1 = norm.ppf(1 - 1 / n_trials)
    z2 = norm.ppf(1 - 1 / (n_trials * np.e))
    return float(sr_std * ((1 - _EULER) * z1 + _EULER * z2))


def deflated_sharpe(
    observed_sr: float,
    n_obs: int,
    n_trials: int,
    sr_std: float,
    *,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio(확률 0~1). 다중비교(n_trials)·표본수(n_obs)·비정규성 보정.

    observed_sr·sr_std는 *비연율(per-observation)* Sharpe 기준. 1에 가까울수록 운이 아님.
    """
    if n_obs < 2:
        return 0.0
    sr0 = expected_max_sharpe(n_trials, sr_std)
    denom = np.sqrt(1 - skew * observed_sr + (kurtosis - 1) / 4 * observed_sr**2)
    if denom <= 0:
        return 0.0
    z = (observed_sr - sr0) * np.sqrt(n_obs - 1) / denom
    return float(norm.cdf(z))


def pbo_cscv(perf: np.ndarray, n_splits: int = 10) -> float:
    """PBO via Combinatorially Symmetric Cross-Validation (Bailey 2014).

    perf: shape (T, N) — T 기간 × N 파라미터조합의 기간별 성과(예 기간수익). IS 최고 조합이
    OOS에서 중앙값 이하로 떨어지는 확률을 추정. ≥0.5면 과최적화로 간주.
    """
    perf = np.asarray(perf, dtype=float)
    t, n = perf.shape
    if n < 2 or t < n_splits or n_splits % 2 != 0:
        raise ValueError("perf는 (T≥n_splits, N≥2), n_splits는 짝수여야 함")
    blocks = np.array_split(np.arange(t), n_splits)
    half = n_splits // 2
    lambdas = []
    for combo in combinations(range(n_splits), half):
        is_rows = np.concatenate([blocks[i] for i in combo])
        oos_rows = np.concatenate([blocks[i] for i in range(n_splits) if i not in combo])
        best = int(np.argmax(perf[is_rows].mean(axis=0)))          # IS 최고 조합
        oos = perf[oos_rows].mean(axis=0)
        rank = (oos < oos[best]).sum() / (n - 1)                   # OOS 상대순위 0~1
        w = min(max(rank, 1e-9), 1 - 1e-9)
        lambdas.append(np.log(w / (1 - w)))
    return float((np.array(lambdas) <= 0).mean())


def combo_cum_returns(perf: np.ndarray) -> np.ndarray:
    """각 후보(열)의 OOS 누적수익 = Π(1+구간수익)−1. perf shape (n_splits, n_candidates)."""
    return np.prod(1.0 + np.asarray(perf, dtype=float), axis=0) - 1.0


def _grid_signature(params: dict, grid_keys: list) -> tuple:
    """grid 손잡이 값만 뽑은 식별 서명 — 후보 dict ↔ perf 열 매칭용."""
    return tuple(params[s][k] for (s, k) in grid_keys)


def sensitivity_no_cliff(
    perf: np.ndarray,
    candidates: list[dict],
    grid: dict,
    ref_params: dict,
    *,
    drop_tol: float = 0.5,
) -> bool:
    """민감도 절벽 검정(10-1 ③) — 추천 파라미터를 grid상 한 칸씩 흔든 이웃의 안정성.

    추천(ref)의 각 손잡이를 인접 grid 값으로 바꾼 이웃 조합들의 OOS 누적수익이 추천 대비
    급락하지 않으면(모두 ref의 (1−drop_tol)배 이상) '절벽 없음'. 절벽 = 추천만 외딴 봉우리라
    주변이 급락하는 상태 = 과최적화 신호. ref 누적≤0이면 평가 의미 없어 False(보수).

    perf 열 순서는 candidates(= tune.param_grid)와 동일해야 한다.
    """
    perf = np.asarray(perf, dtype=float)
    if perf.ndim != 2 or perf.shape[1] != len(candidates):
        raise ValueError("perf shape (n_splits, n_candidates)가 candidates와 불일치")
    grid_keys = list(grid)
    rets = combo_cum_returns(perf)
    sig_to_idx = {_grid_signature(c, grid_keys): i for i, c in enumerate(candidates)}
    ref_sig = _grid_signature(ref_params, grid_keys)
    if ref_sig not in sig_to_idx:
        return False
    ref_ret = rets[sig_to_idx[ref_sig]]
    if ref_ret <= 0:
        return False
    floor = ref_ret * (1.0 - drop_tol)
    for axis, (s, k) in enumerate(grid_keys):
        vals = list(grid[(s, k)])
        cur_idx = vals.index(ref_sig[axis])
        for ni in (cur_idx - 1, cur_idx + 1):           # grid상 ±1칸 이웃
            if 0 <= ni < len(vals):
                nsig = ref_sig[:axis] + (vals[ni],) + ref_sig[axis + 1:]
                idx = sig_to_idx.get(nsig)
                if idx is not None and rets[idx] < floor:
                    return False
    return True


def dsr_confidence_tier(dsr: float) -> str:
    """DSR(0~1)을 소액 실전 자본 램프업 신뢰도 등급으로 (10-1, Phase 7.5/10).

    하드 게이트가 아니라 *시작 자본 보수성* 입력 — 낮을수록 시작 자본을 작게·증액을 느리게.
      high (≥0.90)        통계적으로도 강함 → 표준 램프업
      medium (0.50~0.90)  관측이 우연 기대를 넘음 → 보수적 시작
      conservative (<0.50) 우연 가능성이 더 큼 → 최소 자본·느린 증액
    """
    if dsr >= 0.90:
        return "high"
    if dsr >= 0.50:
        return "medium"
    return "conservative"


@dataclass
class GateResult:
    passed: bool
    checks: dict[str, bool]
    dsr: float = 0.0                       # 보조지표(게이트 축 아님) — 신뢰도 등급 입력
    dsr_tier: str = "conservative"         # dsr_confidence_tier(dsr)


def directional_gate(
    *,
    strategy_score: float,
    benchmark_scores: dict[str, float],
    pbo: float,
    sensitivity_no_cliff: bool,
    stress_beats_benchmarks: bool,
    dsr: float = 0.0,
) -> GateResult:
    """하드 3조건 AND. strategy_score·benchmark_scores는 net 기준 동일 지표(누적수익/Sharpe).

    dsr은 게이트 축이 아니라 *정보*로 받아 GateResult에 보존·등급화한다(자본 램프업 신뢰도).
    """
    checks = {
        "beats_all_benchmarks": all(strategy_score > b for b in benchmark_scores.values()),
        "pbo_below_50pct": pbo < 0.5,
        "robust": sensitivity_no_cliff and stress_beats_benchmarks,
    }
    return GateResult(
        passed=all(checks.values()), checks=checks,
        dsr=dsr, dsr_tier=dsr_confidence_tier(dsr),
    )
