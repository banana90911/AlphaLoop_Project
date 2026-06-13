"""Go/No-Go 게이트 — 과최적화 검정 + 방향성 게이트 (eval/gate, 10-1 P1-5).

개인 퀀트가 돈을 잃는 가장 흔한 경로 = 검증을 자기합리화로 건너뛰고 실거래로 가는 것.
그래서 자금과 무관한 **방향성(상대) 게이트**를 코드로 고정한다. 넷 중 하나라도 미달이면
절대 임계와 무관하게 No-Go:
  ① 4종 벤치마크를 전부 초과
  ② Deflated Sharpe Ratio가 신뢰수준 초과(다중비교·시계열길이로 운 보정)
  ③ PBO(Probability of Backtest Overfitting) < 50%
  ④ 파라미터 ±20% 민감도에 절벽 없음 + 거래비용 2배 스트레스에서도 벤치마크 초과
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


@dataclass
class GateResult:
    passed: bool
    checks: dict[str, bool]


def directional_gate(
    *,
    strategy_score: float,
    benchmark_scores: dict[str, float],
    dsr: float,
    pbo: float,
    sensitivity_no_cliff: bool,
    stress_beats_benchmarks: bool,
    dsr_threshold: float = 0.95,
) -> GateResult:
    """4조건 AND. strategy_score·benchmark_scores는 net 기준 동일 지표(누적수익 또는 Sharpe)."""
    checks = {
        "beats_all_benchmarks": all(strategy_score > b for b in benchmark_scores.values()),
        "deflated_sharpe": dsr > dsr_threshold,
        "pbo_below_50pct": pbo < 0.5,
        "robust": sensitivity_no_cliff and stress_beats_benchmarks,
    }
    return GateResult(passed=all(checks.values()), checks=checks)
