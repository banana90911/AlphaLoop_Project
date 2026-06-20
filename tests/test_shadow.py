"""반사실 가상 손익 — 청산 시뮬·적재 (memory/shadow, 07 7.18)."""
from datetime import date, timedelta

import pytest

from config.settings import load_params
from memory import journal
from memory.db import init_db
from memory.shadow import record_shadow, simulate_shadow


@pytest.fixture
def params():
    return load_params("risk_params")


@pytest.fixture
def tax():
    return load_params("tax_rates")


def _bars(closes, atr=2000.0, start=date(2024, 1, 2)):
    return [(start + timedelta(days=i), c, atr) for i, c in enumerate(closes)]


def test_shadow_stop_hit(params, tax):
    # 진입 100,000 손절 96,000 → 95,000으로 떨어지면 손절 청산(손실)
    bars = _bars([98_000, 95_000, 99_000])
    r = simulate_shadow(100_000, 96_000, bars, params=params,
                        entry_date=date(2024, 1, 1), tax_params=tax)
    assert r["return_pct"] < 0
    assert r["virtual_exit_price"] == 95_000


def test_shadow_open_end_when_no_trigger(params, tax):
    # 손절도 안 닿고 청산 트리거 없이 끝 → 마지막가 평가, open_end
    bars = _bars([100_500, 101_000, 100_800], atr=5000.0)
    r = simulate_shadow(100_000, 90_000, bars, params=params,
                        entry_date=date(2024, 1, 1), tax_params=tax)
    assert r["exit_reason"] == "open_end"
    assert r["virtual_exit_price"] == 100_800


def test_shadow_cost_reduces_return(params, tax):
    # 비용 차감으로 gross보다 net이 작다(동가 청산이면 음수)
    bars = _bars([100_000])
    r = simulate_shadow(100_000, 90_000, bars, params=params,
                        entry_date=date(2024, 1, 1), tax_params=tax)
    assert r["return_pct"] < 0      # gross 0 − 왕복비용


def test_record_shadow(params, tax, tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    journal.create_cycle(conn, "c1", "scheduled", None)
    conn.execute(
        "INSERT INTO decisions(decision_id, cycle_id, action, source, decided_at) "
        "VALUES (?,?,?,?,?)", ("d1", "c1", "no_trade", "backtest", "2024-01-01"),
    )
    sim = simulate_shadow(100_000, 96_000, _bars([95_000]), params=params,
                          entry_date=date(2024, 1, 1), tax_params=tax)
    record_shadow(conn, "s1", decision_id="d1", symbol="005930",
                  reject_reason="dissent<0.6", entry=100_000, stop=96_000, target=110_000,
                  sim=sim, regime_tag="bull", created_at="2024-01-01", source="backtest")
    conn.commit()
    row = conn.execute("SELECT virtual_pnl, size_sim_applied FROM shadow_outcomes "
                       "WHERE shadow_id='s1'").fetchone()
    assert row[0] == sim["return_pct"] and row[1] == 0
