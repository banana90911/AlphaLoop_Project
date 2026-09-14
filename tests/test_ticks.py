"""
description:        KRX 호가가격단위 정렬 (주문 거부 방지)
author:             siheon jung
created date:       2026/09/14
remarks:            손절가는 `종가 − k×ATR`이라 거의 항상 단위에 안 맞는다. 여기가
                    틀리면 실계좌에서 스톱이 거부되고 맨몸 포지션이 된다.
"""

import pytest

from core import ticks

# (가격, 호가단위) — 2023-01-25 개정표의 7구간 경계를 모두 짚는다
_TIER_CASES = [
    (1, 1), (1_999, 1),
    (2_000, 5), (4_999, 5),
    (5_000, 10), (19_999, 10),
    (20_000, 50), (49_999, 50),
    (50_000, 100), (199_999, 100),
    (200_000, 500), (499_999, 500),
    (500_000, 1_000), (1_234_567, 1_000),
]


@pytest.mark.parametrize("price,expected", _TIER_CASES)
def test_구간별_호가단위(price, expected):
    assert ticks.tick_size(price) == expected


def test_손절가는_내림으로_맞춘다():
    """올림하면 손절선이 진입가 쪽으로 당겨져 모델보다 일찍 털린다."""
    assert ticks.align_down(27_342) == 27_300      # 2만~5만 → 50원
    assert ticks.align_down(71_384) == 71_300      # 5만~20만 → 100원
    assert ticks.align_down(4_999) == 4_995        # 2천~5천 → 5원


def test_내림은_한_틱을_넘지_않는다():
    """한 틱 넘게 내려가면 R이 의도보다 커진다."""
    for price in (2_001, 7_777, 33_333, 88_888, 234_567, 777_777):
        gap = price - ticks.align_down(price)
        assert 0 <= gap < ticks.tick_size(price)


def test_이미_맞는_가격은_그대로():
    for price in (1_500, 2_000, 5_000, 27_300, 50_000, 71_300, 200_000):
        assert ticks.align_down(price) == price
        assert ticks.is_aligned(price)


def test_정렬_결과는_언제나_유효한_호가다():
    """내림이 아래 구간으로 넘어가 무효한 값이 되면 안 된다(구간 하한이 배수여야 성립)."""
    for price in range(1, 300_000, 617):
        assert ticks.is_aligned(ticks.align_down(price)), price


def test_올림도_유효한_호가를_준다():
    """구간 경계를 넘는 올림(19,995 → 20,000)이 무효해지지 않아야 한다."""
    for price in range(1, 300_000, 617):
        up = ticks.align_up(price)
        assert up >= price
        assert ticks.is_aligned(up), price


def test_0과_음수는_0():
    """가격이 없는 경우 — 호출부가 이 값으로 주문을 내지 않는다는 전제다."""
    assert ticks.align_down(0) == 0
    assert ticks.align_down(-100) == 0
    assert not ticks.is_aligned(0)
