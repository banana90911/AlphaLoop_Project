"""매매 사이클 8단계 오케스트레이션 (03-arch 3.1).

상태머신(`intent`→`ordering`→`recorded`)을 축으로, 부품이 준비된 단계부터 채운다.
- 1단계(후보 선별): `market_data`가 주어지면 작동, 없으면 빈 사이클(상태머신만).
- 5단계(결정): `account`까지 주어지면 결정 파이프라인(A/B/C)을 *드라이런*으로 돌린다.
- 6단계(리스크): 사이클 레벨 게이트(`screen_cycle`)로 신규 차단/스킵/정지를 판정한다.

드라이런 경계: 7단계 실주문 송출은 `exec` 미구현이라 *하지 않는다*(결정 JSON까지만).
종목별 금액 게이트(`screen_order`·`detect_anomaly`)와 수량 환산(`sizing`)·decisions 상세
적재는 다음 증분 — 여기선 사이클 레벨 게이트와 결정 산출까지 배선한다(12 Phase 4 게이트).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from agents.code_decider import Candidate
from config.settings import load_params
from core.schemas import DeciderOutput, OrderAction
from core.timeutils import now_utc
from memory import journal
from pipeline import screening
from pipeline.decision import run_decision
from risk.risk_engine import Account, MarketState, screen_cycle


@dataclass
class CycleResult:
    """사이클 산출. cycle_id는 06 cycles 키, 나머지는 드라이런 결정 결과.

    cycle_action ∈ {proceed, new_blocked, skip, halt}(screen_cycle 판정). decision은
    account가 주어진 정기 사이클에서만 채워지고, 그 외(이벤트·빈 사이클)는 None이다.
    """
    cycle_id: str
    watchlist: list[str] = field(default_factory=list)
    decision: DeciderOutput | None = None
    cycle_action: str = "proceed"
    blocked_reason: str = ""


def new_cycle_id(now=None) -> str:
    """timestamp 기반 cycle_id 발급(06 cycles)."""
    return (now or now_utc()).strftime("%Y%m%dT%H%M%S%fZ")


def _drop_new_entries(out: DeciderOutput) -> DeciderOutput:
    """신규/추가(buy·add) 제거 — 서킷브레이커 시 보유 동적관리만 허용(A.1 4 new_blocked)."""
    kept = [o for o in out.orders if o.action not in (OrderAction.BUY, OrderAction.ADD)]
    return DeciderOutput(orders=kept, notes=out.notes)


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
        # halt/skip은 결정 자체를 하지 않음(매매 중단/사이클 스킵)

    journal.advance_status(conn, cycle_id, "ordering")
    # 7단계: 주문 송출 — 드라이런(미구현, 차단). exec 붙으면 여기서 decision을 집행.

    journal.advance_status(conn, cycle_id, "recorded")
    # 8단계: 기록 — cycles 상태(decisions 상세 적재는 journal 확장 후).
    return CycleResult(cycle_id, watchlist, decision, cycle_action, blocked_reason)
