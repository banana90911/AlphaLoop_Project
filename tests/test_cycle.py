"""
description:        사이클 상태머신·idempotency + 결정·리스크 게이트 배선
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import copy
from datetime import date

import numpy as np
import pandas as pd

from config.settings import load_params
from core.schemas import OrderAction, ProposedOrder
from memory import journal
from pipeline import cycle
from pipeline.cycle import PlannedOrder
from pipeline.gates import Quote
from risk.risk_engine import Account, MarketState, Position, StockStatus


def _quotes(**by_code) -> dict[str, Quote]:
    """{코드: (현재가, 상태)} → Quote 맵. 현재가 생략 시 전일 종가를 안 쓰므로 명시한다."""
    return {c: (v if isinstance(v, Quote) else Quote(v, StockStatus()))
            for c, v in by_code.items()}


def _entry_params(**over) -> dict:
    """진입 임계를 푼 파라미터 사본(캐시 원본 오염 방지 — lru_cache)."""
    p = copy.deepcopy(load_params("risk_params"))
    p["decision"]["entry_threshold"] = 0.0
    for section, values in over.items():
        p[section].update(values)
    return p


# n=300은 12-1 모멘텀 워밍업(252+20)을 확보하기 위한 값이다
def _series(start: float, step: float, n: int = 300) -> pd.DataFrame:
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


def test_cycle_reaches_recorded(conn):
    res = cycle.run(conn)
    status = conn.execute(
        'SELECT "Status" FROM "Cycles" WHERE "CycleId"=%s', (res.cycle_id,)
    ).fetchone()["Status"]
    assert status == "recorded"
    assert res.decision is None          # account 없으면 결정 단계 미실행


def test_recover_marks_pending_failed(conn):
    journal.create_cycle(conn, "STUCK")  # intent로 방치(프로세스 사망 모사)
    recovered = journal.recover_pending_cycles(conn)
    assert recovered == ["STUCK"]
    status = conn.execute(
        'SELECT "Status" FROM "Cycles" WHERE "CycleId"=\'STUCK\''
    ).fetchone()["Status"]
    assert status == "failed"


def test_recover_noop_when_clean(conn):
    cycle.run(conn)  # 완료 사이클만 존재
    assert journal.recover_pending_cycles(conn) == []


def test_advance_status_rejects_unknown(conn):
    journal.create_cycle(conn, "C1")
    try:
        journal.advance_status(conn, "C1", "bogus")
        raise AssertionError("unknown status가 허용됨")
    except ValueError:
        pass


# ── 3~4단계 배선 (결정 규칙·게이트 결정론 검증) ──

def test_decision_runs_for_scheduled_with_account(conn):
    # equity 1천만원: anomaly 신규주문 폭주 임계(1천만원당 5건)에 걸리지 않는 규모
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc)
    assert res.cycle_action == "proceed"
    assert res.decision is not None                  # 결정 단계 실행됨(드라이런)


def test_no_account_skips_decision(conn):
    res = cycle.run(conn, market_data=_universe())   # account 없음
    assert res.decision is None                      # 워치리스트까지만
    assert set(res.watchlist)


def test_circuit_breaker_blocks_new_entries(conn):
    p = copy.deepcopy(load_params("risk_params"))    # 캐시 원본 오염 방지(lru_cache)
    p["decision"]["entry_threshold"] = 0.0           # 정상이면 모든 후보 buy 시도
    acc = Account(start_capital=1_000_000, cash=940_000)  # 당일 -6% → daily_loss 발동
    res = cycle.run(conn, market_data=_universe(), account=acc, params=p)
    assert res.cycle_action == "new_blocked"
    assert all(
        o.action not in (OrderAction.BUY, OrderAction.ADD)
        for o in res.decision.orders
    )                                                # 신규 진입 전부 제거(보유 관리만)


def test_balance_mismatch_halts(conn):
    acc = Account(start_capital=1_000_000, cash=1_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc,
                    market_state=MarketState(balance_ok=False))
    assert res.cycle_action == "halt"
    assert res.decision is None                      # 잔고 불일치 → 결정 안 함


# ── 4단계 후반: sizing 환산 + 이상행동 게이트(드라이런 집행 계획) ──

def test_sizing_produces_planned_orders(conn):
    p = copy.deepcopy(load_params("risk_params"))
    p["decision"]["entry_threshold"] = 0.0           # 상승 후보 buy 시도
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=p)
    assert res.cycle_action == "proceed"
    assert res.planned_orders                         # 집행 계획 산출됨
    for o in res.planned_orders:
        assert o.qty > 0 and o.price > 0
        assert o.stop < o.price                       # 손절은 진입가 아래(롱)
        assert o.code in {"UP1", "UP2"}               # 하락(DN·FLAT)은 모멘텀 게이트로 배제


def test_anomaly_gate_safe_stops(conn):
    p = copy.deepcopy(load_params("risk_params"))
    p["decision"]["entry_threshold"] = 0.0
    p["anomaly"]["single_order_pct"] = 0.001          # 어떤 주문도 이상으로 판정되게
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=p)
    assert res.cycle_action == "halt"                 # SafeStop
    assert res.planned_orders == []                   # 집행 계획 비움


# ── 8단계 기록: 결정 제안 decisions 적재 ──

def test_decisions_persisted(conn):
    p = copy.deepcopy(load_params("risk_params"))
    p["decision"]["entry_threshold"] = 0.0
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=p)
    rows = conn.execute(
        'SELECT "SymbolId", "Action", "Reason", "StopPrice", "Quantity", "TargetPositions" '
        'FROM "Decisions" WHERE "CycleId"=%s',
        (res.cycle_id,),
    ).fetchall()
    assert rows                                        # 결정이 DB에 남음
    buys = [r for r in rows if r["Action"] == "buy"]
    assert buys and all(r["Reason"] == "entryThreshold" for r in buys)
    assert all(r["TargetPositions"] > 0 for r in rows)  # 동일가중 배분의 분모
    # buy의 StopPrice·Quantity는 집행 계획(planned)에서 채워짐
    planned_codes = {o.code for o in res.planned_orders}
    for r in buys:
        if r["SymbolId"] in planned_codes:
            assert r["StopPrice"] is not None and r["StopPrice"] > 0
            assert r["Quantity"] > 0


def test_no_decision_no_rows(conn):
    res = cycle.run(conn, market_data=_universe())     # account 없음 → 결정 없음
    rows = conn.execute(
        'SELECT 1 FROM "Decisions" WHERE "CycleId"=%s', (res.cycle_id,)
    ).fetchall()
    assert rows == []


def test_record_decisions_maps_actions_and_skips_hold(conn):
    journal.create_cycle(conn, "C1")
    orders = [
        ProposedOrder(code="A", action=OrderAction.BUY, risk_budget=0.5),
        ProposedOrder(code="B", action=OrderAction.TRIM, risk_budget=0.3),
        ProposedOrder(code="C", action=OrderAction.HOLD),     # 적재 생략 대상
        ProposedOrder(code="D", action=OrderAction.SELL),
    ]
    ids = journal.record_decisions(
        conn, "C1", orders, plans={"A": PlannedOrder("A", 2, 100.0, 95.0)}
    )
    assert len(ids) == 3                                       # hold 제외
    rows = {
        r["SymbolId"]: r for r in conn.execute(
            'SELECT "SymbolId", "Action", "Reason", "StopPrice", "RiskPerShare" '
            'FROM "Decisions" WHERE "CycleId"=\'C1\''
        )
    }
    assert rows["A"]["Action"] == "buy" and rows["A"]["StopPrice"] == 95.0
    assert rows["A"]["RiskPerShare"] == 5.0                    # R = 100 − 95
    # 부분 청산(exitPartial)은 규칙에서 뺐다 — trim도 전량 청산으로 기록된다
    assert rows["B"]["Action"] == "exitAll" and rows["B"]["Reason"] == "thesisInvalid"
    assert rows["D"]["Action"] == "exitAll"
    assert "C" not in rows                                     # hold 미적재


# ── 대시보드 공급: 사이클이 자기가 한 일을 남기는가 (07-model 7.2) ──

def _rich_account() -> Account:
    return Account(start_capital=10_000_000, cash=8_000_000,
                   positions=[Position("FLAT", 2000, 1000.0)])


def test_account_snapshot_recorded(conn):
    acc = _rich_account()
    res = cycle.run(conn, market_data=_universe(), account=acc)
    row = conn.execute(
        'SELECT * FROM "AccountSnapshots" WHERE "CycleId"=%s', (res.cycle_id,)
    ).fetchone()
    assert row is not None
    assert row["Amount"] == 8_000_000                 # 예수금
    assert row["PositionValue"] == 2_000_000          # 보유 평가금액
    assert row["TotalAsset"] == 10_000_000
    assert row["BaseAsset"] == 10_000_000
    assert abs(row["DayReturnPercent"]) < 1e-9        # 기준선과 같으면 0%


def test_account_snapshot_computes_day_return(conn):
    acc = Account(start_capital=10_000_000, cash=9_000_000)   # 당일 −10%
    res = cycle.run(conn, market_data=_universe(), account=acc)
    row = conn.execute(
        'SELECT "DayReturnPercent" FROM "AccountSnapshots" WHERE "CycleId"=%s',
        (res.cycle_id,),
    ).fetchone()
    assert abs(row["DayReturnPercent"] + 0.10) < 1e-9


def test_no_snapshot_without_account(conn):
    res = cycle.run(conn, market_data=_universe())
    assert conn.execute(
        'SELECT 1 FROM "AccountSnapshots" WHERE "CycleId"=%s', (res.cycle_id,)
    ).fetchall() == []


def test_cycle_scores_recorded_for_watchlist(conn):
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc)
    rows = {
        r["SymbolId"]: r for r in conn.execute(
            'SELECT * FROM "CycleScores" WHERE "CycleId"=%s', (res.cycle_id,)
        )
    }
    assert set(rows) == set(res.watchlist)
    up1 = rows["UP1"]
    assert up1["Inclusion"] == "topRank"
    assert up1["LastPrice"] > 0 and up1["Atr"] > 0
    # StopWidth = stop_atr_k × ATR (06-sizing 6.1)
    assert abs(up1["StopWidth"] - 2.0 * up1["Atr"]) < 1e-6
    assert up1["TotalScore"] is not None


def test_cycle_scores_mark_holdings(conn):
    acc = _rich_account()
    res = cycle.run(conn, market_data=_universe(), account=acc, holdings=("FLAT",))
    row = conn.execute(
        'SELECT "Inclusion" FROM "CycleScores" WHERE "CycleId"=%s AND "SymbolId"=\'FLAT\'',
        (res.cycle_id,),
    ).fetchone()
    assert row["Inclusion"] == "holding"       # 보유는 점수와 무관하게 편입된다


def test_cycle_scores_record_block_reason(conn):
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    quotes = {"UP1": Quote(6980.0, StockStatus(suspended=True)),
              "UP2": Quote(3990.0, StockStatus())}
    # FLAT은 보유라 워치리스트에 들어오지만 시세 맵에는 없다(조회 실패 모사)
    res = cycle.run(conn, market_data=_universe(), account=acc,
                    holdings=("FLAT",), quotes=quotes)
    rows = {
        r["SymbolId"]: r for r in conn.execute(
            'SELECT * FROM "CycleScores" WHERE "CycleId"=%s', (res.cycle_id,)
        )
    }
    assert rows["UP1"]["BlockReason"] == "halted" and rows["UP1"]["IsTradable"] is False
    assert rows["UP2"]["BlockReason"] is None and rows["UP2"]["IsTradable"] is True
    # 상태를 모르는 종목은 거래 가능으로 적지 않는다
    assert rows["FLAT"]["IsTradable"] is None


# ── 4단계 게이트 판정이 RiskChecks로 남는가 ──

def _checks(conn, cycle_id) -> list[dict]:
    return conn.execute(
        'SELECT * FROM "RiskChecks" WHERE "CycleId"=%s ORDER BY "CheckOrder"', (cycle_id,)
    ).fetchall()


def test_cycle_level_pass_recorded(conn):
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc)
    cycle_rows = [r for r in _checks(conn, res.cycle_id) if r["DecisionId"] is None]
    assert len(cycle_rows) == 1                       # 사이클 단위 판정은 한 줄
    assert cycle_rows[0]["CheckName"] == "circuitBreaker"
    assert cycle_rows[0]["Result"] == "pass"


def test_balance_mismatch_recorded_as_safe_stop(conn):
    acc = Account(start_capital=1_000_000, cash=1_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc,
                    market_state=MarketState(balance_ok=False))
    row = _checks(conn, res.cycle_id)[0]
    assert (row["CheckOrder"], row["CheckName"]) == (1, "balanceSync")
    assert row["Result"] == "safeStop"


def test_stale_data_recorded_as_safe_stop(conn):
    acc = Account(start_capital=1_000_000, cash=1_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc,
                    market_state=MarketState(prices_ok=False))
    row = _checks(conn, res.cycle_id)[0]
    assert (row["CheckOrder"], row["CheckName"]) == (3, "dataFreshness")
    assert row["Result"] == "safeStop" and res.cycle_action == "halt"


def test_holiday_skips_cycle(conn):
    acc = Account(start_capital=1_000_000, cash=1_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc,
                    market_state=MarketState(halted=True))
    assert res.cycle_action == "skip"
    row = _checks(conn, res.cycle_id)[0]
    assert (row["CheckOrder"], row["CheckName"], row["Result"]) == (2, "marketHalt", "skipCycle")
    status = conn.execute(
        'SELECT "Status", "SkipReason" FROM "Cycles" WHERE "CycleId"=%s', (res.cycle_id,)
    ).fetchone()
    assert status["Status"] == "skipped" and status["SkipReason"]


def test_symbol_state_gate_blocks_entry(conn):
    p = _entry_params()
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    quotes = {"UP1": Quote(6980.0, StockStatus(vi=True)),
              "UP2": Quote(3990.0, StockStatus())}
    res = cycle.run(conn, market_data=_universe(), account=acc, params=p,
                    quotes=quotes)
    assert "UP1" not in {o.code for o in res.planned_orders}   # VI 종목은 진입 제외
    assert "UP2" in {o.code for o in res.planned_orders}
    rows = {
        r["DecisionId"]: r for r in _checks(conn, res.cycle_id)
        if r["DecisionId"] is not None
    }
    blocked = rows[f"{res.cycle_id}_UP1_buy"]
    assert blocked["Result"] == "reject" and "VI" in blocked["Reason"]
    assert blocked["CheckOrder"] == 7


def test_unknown_symbol_state_blocks_entry(conn):
    # 상태 맵은 있는데 그 종목이 빠졌다 = 조회 실패. 정상으로 넘기지 않는다
    p = _entry_params()
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=p,
                    quotes={"UP2": Quote(3990.0, StockStatus())})
    assert {o.code for o in res.planned_orders} == {"UP2"}


def test_quote_fetcher_called_with_watchlist(conn):
    seen: list[list[str]] = []

    def fetcher(codes):
        seen.append(list(codes))
        return {c: Quote(1000.0, StockStatus()) for c in codes}

    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc,
                    params=_entry_params(), quote_fetcher=fetcher)
    assert seen == [res.watchlist]              # 워치리스트로 좁힌 뒤 한 번만 조회


# ── SafeStop 적재·차단 (05-risk 5.3·5.4) ──

def test_anomaly_records_safe_stop_event(conn):
    p = _entry_params(anomaly={"single_order_pct": 0.001})
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=p)
    assert res.safe_stop_id
    row = conn.execute(
        'SELECT * FROM "SafeStopEvents" WHERE "EventId"=%s', (res.safe_stop_id,)
    ).fetchone()
    assert row["ReleasedDateTime"] is None       # 발생 직후엔 미해제 = 지금 정지 중
    assert row["Trigger"] == "auto" and row["CycleId"] == res.cycle_id
    assert journal.active_safe_stop(conn)["EventId"] == res.safe_stop_id


def test_open_safe_stop_blocks_next_cycle_entries(conn):
    journal.record_safe_stop(conn, cause="잔고 불일치", cycle_id=None)
    p = _entry_params()
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=p)
    assert res.cycle_action == "new_blocked"
    assert res.planned_orders == []
    assert all(
        o.action not in (OrderAction.BUY, OrderAction.ADD) for o in res.decision.orders
    )


def test_released_safe_stop_does_not_block(conn):
    event_id = journal.record_safe_stop(conn, cause="데이터 오류")
    journal.release_safe_stop(conn, event_id, released_by="owner", reason="원인 확인 완료")
    assert journal.active_safe_stop(conn) is None
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=_entry_params())
    assert res.cycle_action == "proceed"


def test_safe_stop_cycle_sends_no_orders(conn):
    p = _entry_params(anomaly={"single_order_pct": 0.001})
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=p)
    assert res.order_ids == []
    status = conn.execute(
        'SELECT "Status", "FailedStep" FROM "Cycles" WHERE "CycleId"=%s', (res.cycle_id,)
    ).fetchone()
    assert status["Status"] == "failed" and status["FailedStep"] == 4


# ── 유동성 한도 (06-sizing 6.1 — 백테스트 정본과 같은 제약) ──

def test_liquidity_cap_reduces_quantity(conn):
    # ADV 1% 한도가 동일가중 배분보다 작으면 수량이 깎이고 그 사실이 남는다
    p = _entry_params(limits={"adv_participation": 0.00001})
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=p)
    reduced = [
        r for r in _checks(conn, res.cycle_id) if r["Result"] == "reduce"
    ]
    assert reduced and "유동성 한도" in reduced[0]["Reason"]
    assert reduced[0]["ActualValue"] > reduced[0]["LimitValue"]


def test_liquidity_cap_off_by_default_shape(conn):
    # 기본값(1%)에서는 이 테스트 데이터의 거래대금이 커서 축소가 걸리지 않는다
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=_entry_params())
    assert [r for r in _checks(conn, res.cycle_id) if r["Result"] == "reduce"] == []
    assert res.planned_orders


# ── 3단계 무거래: 기대이익 < 왕복 비용 (06-sizing 6.1) ──

def _flat_universe() -> dict[str, pd.DataFrame]:
    """상승 추세지만 일중 변동이 거의 없는 종목 — ATR이 작아 무거래에 걸린다."""
    idx = pd.bdate_range(end="2024-06-28", periods=300).date
    close = 100_000 + 20 * np.arange(300)
    tiny = pd.DataFrame(
        {"open": close, "high": close * 1.0001, "low": close * 0.9999,
         "close": close, "volume": 1_000_000.0},
        index=pd.Index(idx, name="date"),
    )
    return {"TINY": tiny, "UP1": _series(1000, 20)}


def test_cost_exceeds_edge_blocks_entry(conn):
    acc = Account(start_capital=100_000_000, cash=100_000_000)
    res = cycle.run(conn, market_data=_flat_universe(), account=acc,
                    params=_entry_params())
    codes = {o.code for o in res.planned_orders}
    assert "TINY" not in codes          # 기대이익이 비용을 못 넘어 무거래
    assert "UP1" in codes


def test_no_trade_recorded_with_reason(conn):
    acc = Account(start_capital=100_000_000, cash=100_000_000)
    res = cycle.run(conn, market_data=_flat_universe(), account=acc,
                    params=_entry_params())
    row = conn.execute(
        'SELECT * FROM "Decisions" WHERE "CycleId"=%s AND "SymbolId"=\'TINY\'',
        (res.cycle_id,),
    ).fetchone()
    assert row["Action"] == "noTrade" and row["Reason"] == "costExceedsEdge"
    assert row["NetEdge"] < 0                     # 음수면 무거래(07-model 7.2)
    assert row["EstimatedCost"] > 0


def test_executed_entry_records_edge_columns(conn):
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc, params=_entry_params())
    row = conn.execute(
        'SELECT * FROM "Decisions" WHERE "CycleId"=%s AND "Action"=\'buy\' '
        'AND "Quantity" IS NOT NULL LIMIT 1',
        (res.cycle_id,),
    ).fetchone()
    assert row["NetEdge"] > 0
    assert row["EstimatedCost"] > 0
    assert row["RewardRiskRatio"] > 0             # 비용 빼고 남는 R 배수


def test_no_trade_gets_no_order(conn):
    # noTrade는 DecisionId가 주문에 연결되지 않는다
    acc = Account(start_capital=100_000_000, cash=100_000_000)
    cycle.run(conn, market_data=_flat_universe(), account=acc, params=_entry_params())
    assert conn.execute(
        'SELECT 1 FROM "Orders" WHERE "SymbolId"=\'TINY\''
    ).fetchall() == []


# ── 지표는 전일 확정 봉, 가격은 현재가 (04-data 4.2) ──

def _universe_with_spike() -> dict[str, pd.DataFrame]:
    """마지막 행(=당일 미완성 봉)만 변동폭이 다른 유니버스."""
    md = _universe()
    for df in md.values():
        df.iloc[-1, df.columns.get_loc("high")] = df["close"].iloc[-1] * 1.30
        df.iloc[-1, df.columns.get_loc("low")] = df["close"].iloc[-1] * 0.70
    return md


def test_atr_ignores_todays_unconfirmed_bar(conn):
    """당일 봉을 제외(asof=전일)하면 ATR이 그 봉의 변동폭에 오염되지 않는다."""
    md = _universe_with_spike()
    asof = sorted(md["UP1"].index)[-2]        # 마지막 직전 = 전일
    acc = Account(start_capital=10_000_000, cash=10_000_000)

    dirty = cycle.run(conn, market_data=md, account=acc, params=_entry_params())
    clean = cycle.run(conn, market_data=md, account=acc, asof=asof,
                      params=_entry_params())

    def atr_of(res):
        return conn.execute(
            'SELECT "Atr" FROM "CycleScores" WHERE "CycleId"=%s AND "SymbolId"=\'UP1\'',
            (res.cycle_id,),
        ).fetchone()["Atr"]

    assert atr_of(clean) < atr_of(dirty)      # 당일 급등락 봉이 ATR을 부풀린다


def test_entry_price_comes_from_quote_not_bar(conn):
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    quotes = _quotes(UP1=7500.0, UP2=4200.0)
    res = cycle.run(conn, market_data=_universe(), account=acc,
                    params=_entry_params(), quotes=quotes)
    prices = {o.code: o.price for o in res.planned_orders}
    assert prices["UP1"] == 7500.0            # 전일 종가(6980)가 아니라 현재가
    assert prices["UP2"] == 4200.0


def test_stop_uses_quote_price_and_prev_atr(conn):
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc,
                    params=_entry_params(), quotes=_quotes(UP1=7500.0, UP2=4200.0))
    row = conn.execute(
        'SELECT "Atr" FROM "CycleScores" WHERE "CycleId"=%s AND "SymbolId"=\'UP1\'',
        (res.cycle_id,),
    ).fetchone()
    up1 = next(o for o in res.planned_orders if o.code == "UP1")
    assert abs(up1.stop - (7500.0 - 2.0 * row["Atr"])) < 1e-6


def test_quote_without_price_blocks_entry(conn):
    # 상태는 정상인데 현재가를 못 받았다 → 진입가를 확정할 수 없으므로 진입하지 않는다
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    quotes = {"UP1": Quote(None, StockStatus()), "UP2": Quote(4200.0, StockStatus())}
    result = cycle.run(conn, market_data=_universe(), account=acc,
                       params=_entry_params(), quotes=quotes)
    assert {o.code for o in result.planned_orders} == {"UP2"}


def test_cycle_scores_last_price_is_quote(conn):
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=_universe(), account=acc,
                    quotes=_quotes(UP1=7500.0, UP2=4200.0))
    row = conn.execute(
        'SELECT "LastPrice" FROM "CycleScores" WHERE "CycleId"=%s AND "SymbolId"=\'UP1\'',
        (res.cycle_id,),
    ).fetchone()
    assert row["LastPrice"] == 7500.0


def test_trade_date_separate_from_asof(conn):
    md = _universe()
    asof = sorted(md["UP1"].index)[-2]
    acc = Account(start_capital=10_000_000, cash=10_000_000)
    res = cycle.run(conn, market_data=md, account=acc, asof=asof,
                    trade_date=date(2026, 8, 28))
    row = conn.execute(
        'SELECT "TradeDate" FROM "Cycles" WHERE "CycleId"=%s', (res.cycle_id,)
    ).fetchone()
    assert row["TradeDate"] == date(2026, 8, 28)   # 지표 기준일이 아니라 사이클 날짜
