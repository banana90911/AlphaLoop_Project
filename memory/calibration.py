"""보정 통계 — 수축·Wilson·시간가중 (memory/calibration, 07 7.5·7.6·7.15·7.18).

`calibration` 뷰의 raw 집계(맞은수·표본수)에 *작은/오래된 표본에서 함부로 단정 않는*
보정을 입혀 칸별 신뢰 적중률을 낸다. 이 값이 사이징 켈리 p(05-risk 5-2)·conviction
보정신뢰(w2)·decider 입력으로 흐른다. 회고(LLM) 없이 전부 코드(SQL/numpy) — 비용 0.

핵심: *점추정만 주면 과반응*하므로 수축 적중률과 함께 표본수·Wilson 구간(흐릿함)을 같이
낸다(7.6). 표본이 적으면 prior(0.5)로 끌려가고(7.5), 오래되면 가중이 준다(7.15).
"""
from __future__ import annotations

import math
import sqlite3

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


def calibrated_rate(
    conn: sqlite3.Connection, *, agent_role: str, regime: str | None = None,
    confidence_bucket: int | None = None, prior: float = 0.5, strength: float = 10.0,
) -> dict:
    """`calibration` 뷰에서 칸 raw 합산 → 수축 적중률·표본수·Wilson 구간.

    regime·confidence_bucket이 None이면 그 축을 무시(가장 굵은 칸부터 채운다, 7.18).
    """
    q = ["SELECT COALESCE(SUM(correct_count), 0), COALESCE(SUM(n), 0)",
         "FROM calibration WHERE agent_role = ?"]
    args: list = [agent_role]
    if regime is not None:
        q.append("AND regime = ?")
        args.append(regime)
    if confidence_bucket is not None:
        q.append("AND confidence_bucket = ?")
        args.append(confidence_bucket)
    correct, n = conn.execute(" ".join(q), args).fetchone()
    lo, hi = wilson_interval(correct, n)
    return {
        "rate": shrink(correct, n, prior=prior, strength=strength),
        "n": int(n),
        "ci_low": lo,
        "ci_high": hi,
        "raw_rate": (correct / n if n else None),
    }
