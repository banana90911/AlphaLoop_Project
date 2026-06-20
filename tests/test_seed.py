"""학습 시드 — 켈리 p·b·outcomes 적재 (backtest/seed, 5-2·Phase6)."""
from datetime import date

import pytest

from backtest.engine import ClosedTrade
from backtest.seed import kelly_pb, seed_outcomes
from memory.db import init_db


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


def test_seed_outcomes_inserts(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    trades = [_t("005930", 100, 110, 100), _t("000660", 100, 90, -100)]
    n = seed_outcomes(conn, trades, source="backtest")
    conn.commit()
    assert n == 2
    rows = conn.execute(
        "SELECT symbol, return_pct, source FROM outcomes ORDER BY symbol"
    ).fetchall()
    assert rows[0][0] == "000660" and rows[0][2] == "backtest"
    assert rows[1][1] == pytest.approx(0.10)              # 005930 +10%


def test_seed_feeds_calibration_kelly_chain(tmp_path):
    # 시드한 outcomes로 켈리 p·b가 산출되는 전체 흐름(학습 1일차 활성)
    conn = init_db(str(tmp_path / "t.db"))
    trades = [_t("A", 100, 110, 100), _t("B", 100, 120, 200), _t("C", 100, 90, -100)]
    seed_outcomes(conn, trades)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM outcomes WHERE source='backtest'").fetchone()[0] == 3
    assert kelly_pb(trades) is not None
