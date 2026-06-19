"""포트폴리오 배분 — 동시보유·섹터분산·교체매매·레짐노출 (risk/portfolio, 05-risk 5-2)."""
import pytest

from config.settings import load_params
from risk.portfolio import (
    Holding,
    can_add_sector,
    rotation_candidate,
    target_exposure,
    target_positions,
    weakest,
)


@pytest.fixture
def params():
    return load_params("risk_params")


def test_target_positions_scales_with_capital(params):
    assert target_positions(1_000_000, params) == 4      # 소액
    assert target_positions(40_000_000, params) == 5
    assert target_positions(80_000_000, params) == 8
    assert target_positions(200_000_000, params) == 10    # 1억+


def test_target_exposure_regime(params):
    assert target_exposure(1.0, params) == pytest.approx(1.0)   # 우호 → 만노출
    assert target_exposure(0.0, params) == pytest.approx(0.2)   # 악화 → min
    assert target_exposure(0.5, params) == pytest.approx(0.6)   # 중간
    # 범위 밖 clip
    assert target_exposure(1.5, params) == pytest.approx(1.0)
    assert target_exposure(-1.0, params) == pytest.approx(0.2)


def test_sector_limit_two(params):
    hold = [Holding("A", "반도체", 0.5), Holding("B", "반도체", 0.4)]
    assert not can_add_sector(hold, "반도체", params)     # 이미 2종목
    assert can_add_sector(hold, "바이오", params)         # 다른 섹터 OK


def test_weakest():
    hold = [Holding("A", "S", 0.5), Holding("B", "S", 0.2), Holding("C", "S", 0.8)]
    assert weakest(hold).code == "B"
    assert weakest([]) is None


def test_rotation_empty_slot_no_swap(params):
    hold = [Holding("A", "S", 0.3)]                       # 목표 4, 슬롯 여유
    assert rotation_candidate(hold, 4, 0.9, params) is None


def test_rotation_swaps_weakest_when_full(params):
    hold = [Holding("A", "S", 0.3), Holding("B", "S", 0.5)]
    # 슬롯 2/2, 신규 엣지 0.5 > 최약체 0.3 + margin 0.1 = 0.4 → 교체
    assert rotation_candidate(hold, 2, 0.5, params).code == "A"


def test_rotation_no_swap_if_not_clearly_better(params):
    hold = [Holding("A", "S", 0.3), Holding("B", "S", 0.5)]
    # 신규 0.35 < 0.3 + 0.1 → 교체 안 함
    assert rotation_candidate(hold, 2, 0.35, params) is None
