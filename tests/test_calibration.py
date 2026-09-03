"""
description:        보정 통계 — 수축·Wilson·시간가중 (memory/calibration, 06-sizing 6.1).
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import pytest

from memory.calibration import shrink, time_weighted_rate, wilson_interval


def test_shrink_empty_is_prior():
    assert shrink(0, 0) == pytest.approx(0.5)


def test_shrink_pulls_small_sample_toward_prior():
    # 2/2(=100%)도 단정 않음: (2+5)/(2+10)=0.583
    assert shrink(2, 2, strength=10) == pytest.approx(7 / 12)
    # 큰 표본은 raw에 수렴: 800/1000 ≈ 0.797
    assert shrink(800, 1000, strength=10) == pytest.approx(805 / 1010)


def test_wilson_empty_full_range():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_bounds_within_unit():
    lo, hi = wilson_interval(5, 10)
    assert 0.0 <= lo < 0.5 < hi <= 1.0


def test_time_weighted_recent_dominates():
    # 최근(age 0) 적중 1, 오래된(age 365) 실패 0 → 0.5보다 큼
    assert time_weighted_rate([1, 0], [0, 365], half_life=180) > 0.5
    assert time_weighted_rate([], []) == pytest.approx(0.5)
