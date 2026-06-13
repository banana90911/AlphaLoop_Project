"""워크포워드 분할 — 롤링/앵커드·룩어헤드 차단 (backtest/walkforward, 10-1)."""
from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest

from backtest.walkforward import Split, concat_oos_returns, rolling_splits

_DATES = [date(2020, 1, 1) + timedelta(days=i) for i in range(100)]


def test_rolling_split_count_and_bounds():
    sp = rolling_splits(_DATES, train_size=40, test_size=20)
    # i=0,20,40: 0+40+20=60≤100, 20+60=80≤100, 40+60=100≤100 → 3개
    assert len(sp) == 3
    first = sp[0]
    assert first.train_start == _DATES[0]
    assert first.train_end == _DATES[39]
    assert first.test_start == _DATES[40]
    assert first.test_end == _DATES[59]


def test_test_always_after_train_no_lookahead():
    for s in rolling_splits(_DATES, train_size=30, test_size=10):
        assert s.train_end < s.test_start       # OOS는 항상 train 미래


def test_rolling_window_moves():
    sp = rolling_splits(_DATES, train_size=40, test_size=20)
    # 롤링: train 시작이 전진
    assert sp[1].train_start > sp[0].train_start


def test_anchored_keeps_train_start_fixed():
    sp = rolling_splits(_DATES, train_size=40, test_size=20, anchored=True)
    assert all(s.train_start == _DATES[0] for s in sp)
    # 확장: train_end가 점점 뒤로
    assert sp[1].train_end > sp[0].train_end


def test_step_controls_overlap():
    dense = rolling_splits(_DATES, train_size=40, test_size=20, step=10)
    sparse = rolling_splits(_DATES, train_size=40, test_size=20, step=20)
    assert len(dense) > len(sparse)


def test_invalid_sizes_raise():
    with pytest.raises(ValueError):
        rolling_splits(_DATES, train_size=0, test_size=10)


def test_too_short_returns_empty():
    assert rolling_splits(_DATES[:30], train_size=40, test_size=20) == []


def test_concat_oos():
    assert concat_oos_returns([[1, 2], [3], [4, 5]]) == [1, 2, 3, 4, 5]


def test_split_is_frozen():
    s = Split(_DATES[0], _DATES[1], _DATES[2], _DATES[3])
    with pytest.raises(FrozenInstanceError):
        s.train_start = _DATES[5]
