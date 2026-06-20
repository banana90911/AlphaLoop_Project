"""보정 통계 — 수축·Wilson·시간가중·DB 조회 (memory/calibration, 07)."""
import pytest

from memory import journal
from memory.calibration import (
    calibrated_rate,
    shrink,
    time_weighted_rate,
    wilson_interval,
)
from memory.db import init_db


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


def test_calibrated_rate_from_db(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    journal.create_cycle(conn, "c1", "scheduled", None)
    conn.execute(
        "INSERT INTO decisions(decision_id, cycle_id, action, regime_tag, source, decided_at) "
        "VALUES (?,?,?,?,?,?)",
        ("d1", "c1", "buy", "bull", "backtest", "2026-06-20"),
    )
    for i, correct in enumerate([1, 1, 1, 0, 0]):       # 3/5 적중
        conn.execute(
            "INSERT INTO agent_predictions"
            "(prediction_id, decision_id, symbol, agent_role, confidence, correct, source) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"p{i}", "d1", "005930", "catalyst", 0.8, correct, "backtest"),
        )
    conn.commit()

    r = calibrated_rate(conn, agent_role="catalyst")
    assert r["n"] == 5
    assert r["raw_rate"] == pytest.approx(0.6)
    assert r["rate"] == pytest.approx((3 + 10 * 0.5) / (5 + 10))   # 수축
    assert 0.0 <= r["ci_low"] <= r["ci_high"] <= 1.0


def test_calibrated_rate_regime_filter(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    journal.create_cycle(conn, "c1", "scheduled", None)
    conn.execute(
        "INSERT INTO decisions(decision_id, cycle_id, action, regime_tag, source, decided_at) "
        "VALUES (?,?,?,?,?,?)",
        ("d1", "c1", "buy", "bear", "backtest", "2026-06-20"),
    )
    conn.execute(
        "INSERT INTO agent_predictions"
        "(prediction_id, decision_id, symbol, agent_role, confidence, correct, source) "
        "VALUES (?,?,?,?,?,?,?)",
        ("p0", "d1", "A", "catalyst", 0.8, 1, "backtest"),
    )
    conn.commit()
    assert calibrated_rate(conn, agent_role="catalyst", regime="bull")["n"] == 0
    assert calibrated_rate(conn, agent_role="catalyst", regime="bear")["n"] == 1
