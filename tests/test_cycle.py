"""사이클 상태머신·idempotency + 5~6단계 결정·리스크 게이트 배선 (0-B·12 Phase 4)."""
import copy

import numpy as np
import pandas as pd

from config.settings import load_params
from core.schemas import OrderAction
from memory import journal
from memory.db import init_db
from pipeline.trading_cycle import run_cycle
from risk.risk_engine import Account, MarketState


def _series(start: float, step: float, n: int = 120) -> pd.DataFrame:
    idx = pd.bdate_range(end="2024-06-28", periods=n).date
    close = start + step * np.arange(n)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1_000_000.0},
        index=pd.Index(idx, name="date"),
    )


def _universe() -> dict[str, pd.DataFrame]:
    return {"UP1": _series(1000, 20), "UP2": _series(1000, 10),
            "FLAT": _series(1000, 0.0), "DN": _series(2000, -5)}


def test_cycle_reaches_recorded(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    res = run_cycle(conn)
    status = conn.execute(
        "SELECT status FROM cycles WHERE cycle_id=?", (res.cycle_id,)
    ).fetchone()["status"]
    assert status == "recorded"
    assert res.decision is None          # account 없으면 결정 단계 미실행
    conn.close()


def test_recover_marks_pending_failed(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    journal.create_cycle(conn, "STUCK", "scheduled")  # intent로 방치(프로세스 사망 모사)
    recovered = journal.recover_pending_cycles(conn)
    assert recovered == ["STUCK"]
    status = conn.execute("SELECT status FROM cycles WHERE cycle_id='STUCK'").fetchone()["status"]
    assert status == "failed"
    conn.close()


def test_recover_noop_when_clean(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    run_cycle(conn)  # 완료 사이클만 존재
    assert journal.recover_pending_cycles(conn) == []
    conn.close()


def test_advance_status_rejects_unknown(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    journal.create_cycle(conn, "C1", "scheduled")
    try:
        journal.advance_status(conn, "C1", "bogus")
        raise AssertionError("unknown status가 허용됨")
    except ValueError:
        pass
    conn.close()


# ── 5~6단계 배선 (mode B + 빈 뉴스 = LLM 호출 0, 결정·게이트 결정론 검증) ──

def test_decision_runs_for_scheduled_with_account(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    # equity 1천만원: anomaly 신규주문 폭주 임계(1천만원당 5건)에 걸리지 않는 규모
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = run_cycle(conn, market_data=_universe(), account=acc, mode="B")
    assert res.cycle_action == "proceed"
    assert res.decision is not None                  # 결정 단계 실행됨(드라이런)
    conn.close()


def test_no_account_skips_decision(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    res = run_cycle(conn, market_data=_universe())   # account 없음
    assert res.decision is None                      # 워치리스트까지만
    assert set(res.watchlist)
    conn.close()


def test_circuit_breaker_blocks_new_entries(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    p = copy.deepcopy(load_params("risk_params"))    # 캐시 원본 오염 방지(lru_cache)
    p["decision"]["entry_threshold"] = 0.0           # 정상이면 모든 후보 buy 시도
    acc = Account(start_capital=1_000_000, cash=940_000)  # 당일 -6% → daily_loss 발동
    res = run_cycle(conn, market_data=_universe(), account=acc, mode="B", params=p)
    assert res.cycle_action == "new_blocked"
    assert all(
        o.action not in (OrderAction.BUY, OrderAction.ADD)
        for o in res.decision.orders
    )                                                # 신규 진입 전부 제거(보유 관리만)
    conn.close()


def test_balance_mismatch_halts(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    acc = Account(start_capital=1_000_000, cash=1_000_000)
    res = run_cycle(conn, market_data=_universe(), account=acc, mode="B",
                    market_state=MarketState(balance_ok=False))
    assert res.cycle_action == "halt"
    assert res.decision is None                      # 잔고 불일치 → 결정 안 함
    conn.close()


# ── 6단계 후반: sizing 환산 + 이상행동 게이트(드라이런 집행 계획) ──

def test_sizing_produces_planned_orders(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    p = copy.deepcopy(load_params("risk_params"))
    p["decision"]["entry_threshold"] = 0.0           # 상승 후보 buy 시도
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = run_cycle(conn, market_data=_universe(), account=acc, mode="B", params=p)
    assert res.cycle_action == "proceed"
    assert res.planned_orders                         # 집행 계획 산출됨
    for o in res.planned_orders:
        assert o.qty > 0 and o.price > 0
        assert o.stop < o.price                       # 손절은 진입가 아래(롱)
        assert o.code in {"UP1", "UP2"}               # 하락(DN·FLAT)은 모멘텀 게이트로 배제
    conn.close()


def test_anomaly_gate_safe_stops(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    p = copy.deepcopy(load_params("risk_params"))
    p["decision"]["entry_threshold"] = 0.0
    p["anomaly"]["single_order_pct"] = 0.001          # 어떤 주문도 이상으로 판정되게
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = run_cycle(conn, market_data=_universe(), account=acc, mode="B", params=p)
    assert res.cycle_action == "halt"                 # SafeStop
    assert res.planned_orders == []                   # 집행 계획 비움
    conn.close()
