"""매매 사이클 8단계 오케스트레이션 (03-arch 3.1).

상태머신(`intent`→`ordering`→`recorded`)을 축으로, 부품이 준비된 단계부터 채운다.
- 1단계(후보 선별): `market_data`가 주어지면 작동, 없으면 빈 사이클(상태머신만).
- 5단계(결정): `account`까지 주어지면 결정 파이프라인(A/B/C)을 *드라이런*으로 돌린다.
- 6단계(리스크): 사이클 게이트(`screen_cycle`) → 신규(buy)별 수량 환산(`sizing`) +
  이상행동 게이트(`detect_anomaly`) + 종목당 하드룰로 *집행 계획*(PlannedOrder)을 짠다.

드라이런 경계: 7단계 실주문 송출은 `exec` 미구현이라 *하지 않는다*(집행 계획까지만).
섹터 한도(`screen_order`)는 종목→섹터 매핑이 아직 없어 보류(백테스트 engine도 미적용),
보유 동적관리(trim/sell)의 청산 집행(`exits`)·decisions 상세 적재도 다음 증분이다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from math import floor

import pandas as pd

from agents.code_decider import Candidate
from backtest.engine import build_features
from config.settings import load_params
from config.settings import get_settings
from core.schemas import DeciderOutput, OrderAction
from core.timeutils import now_utc
from data.panel import latest_row
from exec import orders
from exec.orders import Broker
from memory import journal
from pipeline import screening
from pipeline.decision import run_decision
from risk import sizing
from risk.risk_engine import (
    Account,
    MarketState,
    OrderProposal,
    detect_anomaly,
    screen_cycle,
)


@dataclass
class PlannedOrder:
    """드라이런 집행 계획 — sizing 환산 결과(실송출 전). 신규(buy) 진입 한 건."""
    code: str
    qty: int
    price: float
    stop: float
    thesis: str = ""

    @property
    def value(self) -> float:
        return self.qty * self.price


@dataclass
class CycleResult:
    """사이클 산출. cycle_id는 06 cycles 키, 나머지는 드라이런 결정·집행 계획.

    cycle_action ∈ {proceed, new_blocked, skip, halt}(screen_cycle·detect_anomaly).
    decision/planned_orders는 account가 주어진 정기 사이클에서만 채워진다.
    """
    cycle_id: str
    watchlist: list[str] = field(default_factory=list)
    decision: DeciderOutput | None = None
    planned_orders: list[PlannedOrder] = field(default_factory=list)
    cycle_action: str = "proceed"
    blocked_reason: str = ""
    trade_ids: list[str] = field(default_factory=list)   # 7단계 실송출 trades(broker 주입 시)


def new_cycle_id(now=None) -> str:
    """timestamp 기반 cycle_id 발급(06 cycles)."""
    return (now or now_utc()).strftime("%Y%m%dT%H%M%S%fZ")


def _drop_new_entries(out: DeciderOutput) -> DeciderOutput:
    """신규/추가(buy·add) 제거 — 서킷브레이커 시 보유 동적관리만 허용(A.1 4 new_blocked)."""
    kept = [o for o in out.orders if o.action not in (OrderAction.BUY, OrderAction.ADD)]
    return DeciderOutput(orders=kept, notes=out.notes)


def _plan_entries(
    decision: DeciderOutput,
    market_data: dict[str, pd.DataFrame],
    account: Account,
    params: dict,
    asof: date | None,
) -> list[PlannedOrder]:
    """신규(buy) 제안 → 수량 환산 집행 계획. 백테스트 engine 진입과 같은 패턴(룩어헤드 차단).

    각 종목 asof 최신 close·ATR로 stop=close−stop_atr_k·ATR을 세우고, sizing이 변동성
    타깃팅 수량을 낸다(종목당 하드룰을 extra_caps 천장으로). conviction=결정자 risk_budget.
    워밍업 미완(ATR/close 결측)·stop≤0·qty≤0은 무진입(백테스트와 동일 게이트).
    """
    e, lim = params["entry"], params["limits"]
    equity = account.equity
    name_cap_value = lim["per_name_hard_pct"] * equity     # 종목당 하드 상한(금액)
    planned: list[PlannedOrder] = []
    for o in decision.orders:
        if o.action not in (OrderAction.BUY, OrderAction.ADD):
            continue                                       # 신규/추가만(보유관리는 후속 exits)
        df = market_data.get(o.code)
        if df is None or df.empty:
            continue
        row = latest_row(build_features(df), asof)
        if row is None:
            continue
        close, atr, mom = row["close"], row["atr"], row["momentum"]
        # 백테스트 engine과 동일 게이트: 워밍업 미완·하락 모멘텀이면 무진입(규칙 정합)
        if pd.isna(close) or pd.isna(atr) or atr <= 0 or pd.isna(mom) or mom <= 0:
            continue
        stop = close - e["stop_atr_k"] * atr
        if stop <= 0:
            continue
        name_cap_qty = floor(name_cap_value / close) if close > 0 else 0
        qty = sizing.position_qty(
            equity, close, stop, conviction=o.risk_budget,
            extra_caps=(name_cap_qty,), params=params,
        )
        if qty <= 0:
            continue
        planned.append(PlannedOrder(o.code, qty, float(close), float(stop), o.thesis))
    return planned


def run_cycle(
    conn: sqlite3.Connection,
    trigger_type: str = "scheduled",
    trigger_event_id: str | None = None,
    *,
    market_data: dict[str, pd.DataFrame] | None = None,
    holdings: tuple[str, ...] = (),
    asof: date | None = None,
    min_value_traded: float | None = None,
    account: Account | None = None,
    news_bundles: list | None = None,
    market_state: MarketState | None = None,
    mode: str = "C",
    params: dict | None = None,
    source: str = "paper",
    broker: Broker | None = None,
) -> CycleResult:
    """한 사이클 실행. 반환: CycleResult.

    market_data(종목별 OHLCV+수급)가 있으면 1단계 후보 선별을 수행한다. 이벤트 트리거
    사이클은 스크리닝을 건너뛰고 워치리스트=보유로 좁힌다(3.1).

    account가 주어지면 5~6단계(결정·리스크 게이트)를 드라이런으로 돈다 — 정기 사이클에서
    market_data로 워치리스트가 만들어진 경우만(이벤트·빈 사이클은 결정 None).
    """
    cycle_id = new_cycle_id()
    journal.create_cycle(conn, cycle_id, trigger_type, trigger_event_id)

    # 1단계: 후보 선별 → 워치리스트 (wl은 score 보존 — 5단계 Candidate 입력)
    wl: pd.DataFrame | None = None
    if trigger_type == "event":
        watchlist = list(holdings)                       # 이벤트는 보유 방어 전용
    elif market_data:
        wl = screening.select_watchlist(
            market_data, holdings=holdings, asof=asof,
            min_value_traded=min_value_traded, params=params,
        )
        watchlist = list(wl.index)
    else:
        watchlist = list(holdings)                       # 빈 사이클(데이터 미주입)

    # 2단계 데이터는 호출측이 market_data로 주입(운영=data.market_data.fetch_prices).
    # 3단계 기억 검색은 lessons 0건이면 휴면(retrieval) — 결정 입력 미배선(후속).

    decision: DeciderOutput | None = None
    planned: list[PlannedOrder] = []
    cycle_action = "proceed"
    blocked_reason = ""

    # 5~6단계: 결정 + 사이클 리스크 게이트 (정기 + market_data + account 일 때만, 드라이런)
    if account is not None and wl is not None and not wl.empty:
        p = params or load_params("risk_params")
        verdict = screen_cycle(market_state or MarketState(), account, p)   # 6단계 사이클 게이트
        cycle_action, blocked_reason = verdict.action, verdict.reason
        if verdict.action in ("proceed", "new_blocked"):
            candidates = [
                Candidate(code, float(wl.loc[code, "score"])) for code in wl.index
            ]
            # 4단계 뉴스·5단계 결정 (run_decision이 mode로 A/B/C 전환)
            decision = run_decision(
                candidates, news_bundles or [], list(holdings),
                cash=account.cash, equity=account.equity, params=p, mode=mode,
            )
            if verdict.action == "new_blocked":          # 서킷브레이커: 신규 제거
                decision = _drop_new_entries(decision)
            # 6단계 후반: 신규 수량 환산 → 이상행동 게이트(SafeStop)
            planned = _plan_entries(decision, market_data, account, p, asof)
            proposals = [OrderProposal(o.code, "buy", o.value) for o in planned]
            anomaly = detect_anomaly(proposals, account, p)
            if not anomaly:                              # 모델 이상행동 → 전체 정지
                planned, cycle_action, blocked_reason = [], "halt", anomaly.reason
        # halt/skip은 결정 자체를 하지 않음(매매 중단/사이클 스킵)

    # 8단계 일부 선행: 결정 의도를 decisions에 먼저 적재 — 송출 전에 "무엇을 하려 했는지"를
    # 디스크에 남기고(11-2.1 idempotency), trades.decision_id FK가 이를 참조한다.
    decision_ids: dict[str, str] = {}
    if decision is not None:
        stops = {p.code: p.stop for p in planned}
        ids = journal.record_decisions(
            conn, cycle_id, decision.orders, stops=stops, source=source
        )
        # record_decisions의 결정론 키: f"{cycle_id}_{code}_{action}". buy/add 계열만 매핑.
        for o in decision.orders:
            if o.action in (OrderAction.BUY, OrderAction.ADD):
                decision_ids[o.code] = f"{cycle_id}_{o.code}_buy"

    journal.advance_status(conn, cycle_id, "ordering")
    # 7단계: 주문 송출. broker 주입 시 planned를 KIS로 실집행, 미주입이면 드라이런(차단).
    trade_ids: list[str] = []
    if broker is not None and planned:
        order_mode = get_settings().trading_mode
        trade_ids = orders.execute_entries(
            conn, planned, broker=broker, cycle_id=cycle_id,
            decision_ids=decision_ids, order_mode=order_mode, source=source,
        )

    journal.advance_status(conn, cycle_id, "recorded")
    return CycleResult(
        cycle_id, watchlist, decision, planned, cycle_action, blocked_reason, trade_ids
    )
