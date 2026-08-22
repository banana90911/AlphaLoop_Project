"""보정 통계 — 수축·Wilson·시간가중 (memory/calibration, 06-sizing 6.1).

승률처럼 *표본이 적으면 못 믿을* 비율을 다룰 때 쓰는 순수 계산 도구다. 사이징의 켈리
승률 p(변동성 타깃팅 대안 경로)와 성과 진단이 이 값을 입력으로 받는다.

핵심: *점추정만 주면 과반응*하므로 수축 적중률과 함께 표본수·Wilson 구간(흐릿함)을 같이
낸다. 표본이 적으면 prior(0.5)로 끌려가고, 오래되면 가중이 준다.

집계 원천은 `Outcomes` 표다 — 진입 시 점수·레짐이 그 표에 박혀 있어 보정통계 전용 표를
따로 두지 않는다(07-model). 집계 질의는 아직 배선되지 않았다.
"""
from __future__ import annotations

import math

import numpy as np


def shrink(correct: float, n: float, *, prior: float = 0.5, strength: float = 10.0) -> float:
    """베이지안 수축 적중률 (7.5). 표본이 적을수록 prior(중립 0.5)로 끌어당겨 과신을 막는다."""
    return (correct + strength * prior) / (n + strength)


def wilson_interval(correct: float, n: float, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson 신뢰구간 (7.6). 표본이 적거나 비율이 0·100%에 가까워도 안 무너진다."""
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
    """시간 가중 적중률 (7.15). 최근일수록 큰 가중(반감기 half_life일). 표본 없으면 0.5."""
    if not outcomes:
        return 0.5
    lam = math.log(2) / half_life
    weights = np.exp(-lam * np.asarray(ages_days, dtype=float))
    if weights.sum() <= 0:
        return 0.5
    return float(np.average(np.asarray(outcomes, dtype=float), weights=weights))
