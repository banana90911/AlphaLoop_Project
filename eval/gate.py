"""
description:        Go/No-Go 게이트 — 과최적화 검정 + 방향성 게이트
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from dataclasses import dataclass
from itertools import combinations

import numpy as np
from scipy.stats import norm

_EULER = 0.5772156649015329


def expected_max_sharpe(n_trials: int, sr_std: float) -> float:
    """N회 독립 시도에서 우연히 기대되는 최대 Sharpe(Bailey & López de Prado)."""
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
    """Deflated Sharpe Ratio(0~1) — 다중비교·표본수·비정규성을 보정한다."""
    if n_obs < 2:
        return 0.0
    sr0 = expected_max_sharpe(n_trials, sr_std)
    denom = np.sqrt(1 - skew * observed_sr + (kurtosis - 1) / 4 * observed_sr**2)
    if denom <= 0:
        return 0.0
    z = (observed_sr - sr0) * np.sqrt(n_obs - 1) / denom
    return float(norm.cdf(z))


def pbo_cscv(perf: np.ndarray, n_splits: int = 10) -> float:
    """PBO via Combinatorially Symmetric Cross-Validation(Bailey 2014). ≥0.5면 과최적화."""
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
    """각 후보(열)의 OOS 누적수익을 계산한다. perf shape (n_splits, n_candidates)."""
    return np.prod(1.0 + np.asarray(perf, dtype=float), axis=0) - 1.0


def _grid_signature(params: dict, grid_keys: list) -> tuple:
    """grid 손잡이 값만 뽑은 식별 서명(후보 dict ↔ perf 열 매칭용)."""
    return tuple(params[s][k] for (s, k) in grid_keys)


def sensitivity_no_cliff(
    perf: np.ndarray,
    candidates: list[dict],
    grid: dict,
    ref_params: dict,
    *,
    drop_tol: float = 0.5,
) -> bool:
    """민감도 절벽 검정 — 추천 파라미터를 grid상 한 칸씩 흔든 이웃의 안정성을 본다."""
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
    """DSR(0~1)을 소액 실전 자본 램프업 신뢰도 등급(high/medium/conservative)으로 바꾼다."""
    if dsr >= 0.90:
        return "high"
    if dsr >= 0.50:
        return "medium"
    return "conservative"


@dataclass
class GateResult:
    """게이트 판정 결과. dsr은 게이트 축이 아니라 보조지표(신뢰도 등급 입력)."""
    passed: bool
    checks: dict[str, bool]
    dsr: float = 0.0
    dsr_tier: str = "conservative"


def directional_gate(
    *,
    strategy_score: float,
    benchmark_scores: dict[str, float],
    pbo: float,
    sensitivity_no_cliff: bool,
    stress_beats_benchmarks: bool,
    dsr: float = 0.0,
) -> GateResult:
    """하드 3조건(벤치마크·PBO·견고성) AND로 Go/No-Go를 판정한다."""
    checks = {
        "beats_all_benchmarks": all(strategy_score > b for b in benchmark_scores.values()),
        "pbo_below_50pct": pbo < 0.5,
        "robust": sensitivity_no_cliff and stress_beats_benchmarks,
    }
    return GateResult(
        passed=all(checks.values()), checks=checks,
        dsr=dsr, dsr_tier=dsr_confidence_tier(dsr),
    )
