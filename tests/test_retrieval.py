"""인출 스코어러 — 유사도·점수·요약·원시매칭 (memory/retrieval, 07 7.21·P1-6)."""
from datetime import date

import pytest

from config.settings import load_params
from memory import journal
from memory.db import init_db
from memory.retrieval import (
    cosine_similarity,
    recency_weight,
    regime_similarity,
    retrieval_score,
    retrieve,
    summarize_outcomes,
)


@pytest.fixture
def weights():
    return load_params("risk_params")["retrieval"]


def test_cosine_identical():
    assert cosine_similarity([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)


def test_cosine_zero_vector():
    assert cosine_similarity([0, 0], [1, 1]) == 0.0


def test_regime_similarity_normalized():
    assert regime_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert regime_similarity([1, 0], [-1, 0]) == pytest.approx(0.0)


def test_recency_halves_at_half_life():
    assert recency_weight(0) == pytest.approx(1.0)
    assert recency_weight(180, half_life=180) == pytest.approx(0.5)


def test_retrieval_score_regime_dominates(weights):
    hi = retrieval_score(regime_sim=1.0, recency=1.0, weights=weights)
    lo = retrieval_score(regime_sim=0.0, recency=1.0, weights=weights)
    assert hi > lo


def test_summarize_empty():
    assert summarize_outcomes([]) == "유사 과거 사례 없음"


def test_summarize_stats():
    rows = [{"return_pct": 0.1, "exit_reason": "tp1"},
            {"return_pct": -0.05, "exit_reason": "stop"},
            {"return_pct": 0.2, "exit_reason": "tp1"}]
    s = summarize_outcomes(rows)
    assert "3건" in s and "승률 67%" in s and "tp1" in s


def _seed(conn, decision_id, regime, ret, reason, closed):
    journal.create_cycle(conn, f"c_{decision_id}", "scheduled", None)
    conn.execute(
        "INSERT INTO decisions(decision_id, cycle_id, action, regime_tag, source, decided_at) "
        "VALUES (?,?,?,?,?,?)",
        (decision_id, f"c_{decision_id}", "buy", regime, "backtest", closed),
    )
    conn.execute(
        "INSERT INTO outcomes(outcome_id, entry_decision_id, symbol, return_pct, exit_reason, "
        "closed_at, source) VALUES (?,?,?,?,?,?,?)",
        (f"o_{decision_id}", decision_id, "005930", ret, reason, closed, "backtest"),
    )


def test_retrieve_ranks_by_regime_and_recency(weights, tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    _seed(conn, "d1", "bull", 0.10, "tp1", "2026-06-01")    # 같은 레짐·최근
    _seed(conn, "d2", "bear", -0.05, "stop", "2026-06-01")  # 다른 레짐
    _seed(conn, "d3", "bull", 0.08, "tp1", "2020-01-01")    # 같은 레짐·오래됨
    conn.commit()

    out = retrieve(conn, "bull", weights=weights, source="backtest",
                   today=date(2026, 6, 20))
    assert out[0]["regime_tag"] == "bull" and out[0]["closed_at"] == "2026-06-01"
    # bull·최근(d1) > bull·과거(d3) > bear(d2)
    codes = [r["closed_at"] for r in out]
    assert codes.index("2026-06-01") < codes.index("2020-01-01")


def test_retrieve_empty_ok(weights, tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    assert retrieve(conn, "bull", weights=weights, source="backtest") == []
