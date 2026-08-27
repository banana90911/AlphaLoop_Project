"""백테스트 거래 결과 → 켈리 p·b (backtest/seed).

DB 적재는 검증하지 않는다 — 백테스트 결과는 DB에 넣지 않는 것이 설계다(07-model 공통
규칙). 여기 남은 것은 순수 계산뿐이라 DB 서버 없이도 돈다.
"""
from datetime import date

import pytest

from backtest.seed import kelly_pb
from backtest.spec_engine import ClosedTrade


def _t(code, entry, exit_, pnl, d0=date(2024, 1, 2), d1=date(2024, 1, 20)):
    return ClosedTrade(code, d0, d1, entry, exit_, 10, "tp1", pnl)


def test_kelly_pb_basic():
    # 2승(+100,+200) 1패(-100): p=2/3, b=평균이익150/평균손실100=1.5
    trades = [_t("A", 100, 110, 100), _t("B", 100, 120, 200), _t("C", 100, 90, -100)]
    p, b = kelly_pb(trades)
    assert p == pytest.approx(2 / 3)
    assert b == pytest.approx(1.5)


def test_kelly_pb_none_when_no_losses():
    assert kelly_pb([_t("A", 100, 110, 100)]) is None     # 손실 표본 없음
    assert kelly_pb([]) is None
