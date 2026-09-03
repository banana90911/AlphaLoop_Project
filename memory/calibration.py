"""
description:        보정 통계 — 수축·Wilson·시간가중 (표본 신뢰도 계산)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import math

import numpy as np


def shrink(correct: float, n: float, *, prior: float = 0.5, strength: float = 10.0) -> float:
    """베이지안 수축 적중률. 표본이 적을수록 prior(중립 0.5)로 끌어당긴다."""
    return (correct + strength * prior) / (n + strength)


def wilson_interval(correct: float, n: float, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson 신뢰구간. 표본이 적거나 비율이 0·100%에 가까워도 안정적이다."""
    if n <= 0:
        return (0.0, 1.0)
    p = correct / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def time_weighted_rate(
    outcomes: list[float], ages_days: list[float], *, half_life: float = 180.0
) -> float:
    """시간 가중 적중률(최근일수록 큰 가중). 표본 없으면 0.5."""
    if not outcomes:
        return 0.5
    lam = math.log(2) / half_life
    weights = np.exp(-lam * np.asarray(ages_days, dtype=float))
    if weights.sum() <= 0:
        return 0.5
    return float(np.average(np.asarray(outcomes, dtype=float), weights=weights))
