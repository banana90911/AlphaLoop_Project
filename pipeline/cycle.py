"""
description:        매매 사이클 7단계 오케스트레이션 (상태머신 + 선별·결정·게이트·집행)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date

import pandas as pd
import psycopg

from config.settings import get_settings, load_params
from core import costs
from core.schemas import DeciderOutput, OrderAction
from core.timeutils import kst_today, now_utc
from data.sources import universe
from exec import exits, orders
from exec.orders import Broker
from memory import journal
from ops import cashflow, notify
from pipeline import screening
from pipeline.decision import Candidate, run_decision
from pipeline.gates import Quote
from risk import sizing
from risk.risk_engine import (
    CHECK_ORDER,
    Account,
    MarketState,
    OrderProposal,
    StockStatus,
    detect_anomaly,
    screen_cycle,
    screen_order,
)

log = logging.getLogger(__name__)

# 상태를 모르는 종목에 쓰는 기본값. 조회 실패를 '정상'으로 넘기면 거래정지 종목에
# 진입할 수 있으므로, 신규 진입에서는 이 기본값 대신 '모름=진입 불가'로 다룬다.
_UNKNOWN_STATUS = StockStatus()


@dataclass
class CashReconciliation:
    """현금 대조(05-risk 5.2 검사 1-b) 결과 — 판정 입력과 기록 재료를 함께 담는다."""
    residual: float = 0.0          # 실제 예수금 − 기대 예수금 (유입 +, 유출 −)
    expected: float = 0.0
    actual: float = 0.0
    net_external_flow: float = 0.0  # 기준선을 옮길 금액(흡수분은 여기 안 들어간다)
    comparable: bool = False        # 직전 스냅샷이 있어 비교가 성립했는가
    resolution: cashflow.FlowResolution | None = None


def _reconcile_cash(conn: psycopg.Connection, account: Account, *, mode: str) -> CashReconciliation:
    """KIS 실제 예수금과 매매로 설명되는 기대 예수금을 대조한다(부수효과 없음).

    매매는 주식과 현금을 항상 같이 움직인다. 보유 대조를 통과했는데 예수금만
    어긋났다면 그건 매매로 설명할 수 없는 돈 = 외부 입출금이다.
    """
    base = journal.expected_cash(conn, mode=mode)
    if base is None:                      # 첫 사이클 — 비교할 기준이 없다
        return CashReconciliation(actual=account.cash)

    residual = account.cash - base["expected"]
    res = cashflow.classify_residual(residual, account.equity)
    return CashReconciliation(
        residual=residual, expected=base["expected"], actual=account.cash,
        net_external_flow=residual if res.shifts_baseline else 0.0,
        comparable=True, resolution=res,
    )


def _record_cash_flow(
    conn: psycopg.Connection, cycle_id: str, recon: CashReconciliation, *, mode: str,
) -> str | None:
    """대조 결과를 `CashFlows`·`RiskChecks`에 남기고 필요하면 알린다. 반환: FlowId."""
    res = recon.resolution
    if res is None or not recon.comparable:
        return None
    if not res.record:                      # 흡수했고 관찰 모드도 꺼져 있음 — 남길 게 없다
        log.info("현금 잔차 흡수: %+.0f원(임계 미만)", recon.residual)
        return None

    flow_id = journal.record_cash_flow(
        conn, cycle_id, kind=res.kind, amount=recon.residual, source=res.source,
        expected=recon.expected, actual=recon.actual, mode=mode,
        status="confirmed" if res.source == "signature" else "unconfirmed",
    )
    journal.record_risk_check(
        conn, cycle_id=cycle_id, check_order=CHECK_ORDER["cashFlow"],
        check_name="cashFlow", result="flowDetected",
        reason=f"외부 현금흐름 {recon.residual:+,.0f}원({res.kind})",
        actual_value=recon.residual,
    )
    if res.alert:
        notify.notify_cash_flow(
            flow_id, recon.residual, expected=recon.expected, actual=recon.actual,
            kind=res.kind, cycle_id=cycle_id,
        )
    return flow_id


@dataclass
class PlannedOrder:
    """드라이런 집행 계획 한 건 — sizing 환산 결과(실송출 전)."""
    code: str
    qty: int
    price: float
    stop: float
    thesis: str = ""
    estimated_cost: float | None = None    # 왕복 거래비용 추정(원)
    net_edge: float | None = None          # 비용을 뺀 기대 엣지(원)
    reward_risk_ratio: float | None = None  # 비용을 뺀 뒤 남는 R 배수

    @property
    def value(self) -> float:
        return self.qty * self.price


@dataclass
class PendingCheck:
    """게이트 판정 1건 — Decisions 적재 후에 RiskChecks로 흘려보낸다.

    결정 단위 검사는 `RiskChecks.DecisionId`가 `Decisions`를 참조하므로,
    Decisions가 먼저 들어가야 적재할 수 있다(외래키 순서).
    """
    code: str
    check_name: str
    result: str
    reason: str = ""
    limit_value: float | None = None
    actual_value: float | None = None


@dataclass
class CycleResult:
    """사이클 산출 결과 — cycle_id·워치리스트·결정·집행계획·상태를 담는다."""
    cycle_id: str
    watchlist: list[str] = field(default_factory=list)
    decision: DeciderOutput | None = None
    planned_orders: list[PlannedOrder] = field(default_factory=list)
    cycle_action: str = "proceed"
    blocked_reason: str = ""
    order_ids: list[str] = field(default_factory=list)
    safe_stop_id: str | None = None


def new_cycle_id(now=None) -> str:
    """timestamp 기반 CycleId 발급."""
    return (now or now_utc()).strftime("%Y%m%dT%H%M%S%fZ")


def _drop_new_entries(out: DeciderOutput) -> DeciderOutput:
    """신규/추가(buy·add) 주문만 제거하고 나머지는 유지한다."""
    kept = [o for o in out.orders if o.action not in (OrderAction.BUY, OrderAction.ADD)]
    return DeciderOutput(orders=kept, notes=out.notes)


def _f(value) -> float | None:
    """NaN·None을 NULL로 바꾼다."""
    return None if value is None or pd.isna(value) else float(value)


def _entry_price(code: str, row, quotes: dict[str, Quote] | None) -> float | None:
    """진입 기준가 — 사이클 시점 현재가. 시세 미주입(드라이런·테스트)이면 전일 종가."""
    if quotes is None:                       # 시세 경로가 아예 없는 실행(테스트·드라이런)
        return _f(row.get("close"))
    q = quotes.get(code)
    return q.last_price if q is not None else None


def _plan_entries(
    decision: DeciderOutput,
    pnl: pd.DataFrame,
    account: Account,
    params: dict,
    quotes: dict[str, Quote] | None,
    market_map: dict[str, str] | None = None,
    trade_date: date | None = None,
) -> tuple[list[PlannedOrder], list[PendingCheck], dict[str, dict]]:
    """신규(buy/add) 제안을 수량 환산하고 무거래 규칙·종목 게이트를 태운다.

    반환: (집행 계획, 게이트 판정 목록, 무거래로 걸러진 종목의 엣지 값).
    판정은 Decisions 적재 뒤에 기록한다.
    """
    e = params["entry"]
    lim = params["limits"]
    adv_part = float(lim.get("adv_participation", 0.0))
    reward_r = float(params["exits"]["breakeven_R"])
    markets = market_map or {}
    day = trade_date or kst_today()
    equity = account.equity
    slots = int(lim["max_positions"]) - len(account.positions)
    committed = 0.0
    planned: list[PlannedOrder] = []
    checks: list[PendingCheck] = []
    no_trades: dict[str, dict] = {}

    for o in decision.orders:
        if slots <= 0:
            break
        if o.action not in (OrderAction.BUY, OrderAction.ADD):
            continue
        if o.code not in pnl.index:
            continue
        row = pnl.loc[o.code]
        # 지표(ATR·모멘텀·거래대금)는 전일 확정 봉에서 온다 — 장중 미완성 봉으로
        # 계산하면 ATR이 작게 나와 손절선이 진입가에 붙는다(04-data 4.2)
        atr, mom = row.get("atr"), row.get("momentum")
        # 진입가는 사이클 시점 현재가로 확정한다(04-data 4.2 3단계 ①).
        # 시세를 못 받았으면 전일 종가로 대신하지 않는다 — 그건 체결가와 어긋난다
        close = _entry_price(o.code, row, quotes)
        # 워밍업 미완·하락 모멘텀 무진입 (백테스트 정본 spec_engine과 같은 조건)
        if close is None or pd.isna(atr) or atr <= 0 or pd.isna(mom) or mom <= 0:
            continue
        stop = float(close) - e["stop_atr_k"] * float(atr)
        if stop <= 0:
            continue

        # 유동성 한도 — 주문금액이 20일 평균 거래대금의 adv_participation을
        # 넘지 못한다(06-sizing 6.1). 백테스트 정본에만 있던 제약을 실거래에 맞춘다.
        caps: tuple[int, ...] = ()
        adv = row.get("adv20")
        if adv_part > 0 and adv is not None and not pd.isna(adv) and adv > 0:
            caps = (int(float(adv) * adv_part / float(close)),)

        base = sizing.position_qty(
            equity, float(close), stop, conviction=o.risk_budget, params=params,
        )
        qty = sizing.position_qty(
            equity, float(close), stop, conviction=o.risk_budget,
            extra_caps=caps, params=params,
        )
        if qty <= 0:
            checks.append(PendingCheck(
                o.code, "hardLimit", "reject", "수량 0(배분액·유동성 한도 미달)",
                actual_value=float(base),
            ))
            continue
        if caps and qty < base:
            checks.append(PendingCheck(
                o.code, "hardLimit", "reduce",
                f"유동성 한도(ADV {adv_part:.1%})로 {base}→{qty}주 축소",
                limit_value=float(caps[0]), actual_value=float(base),
            ))

        # 3단계 무거래 — 기대이익이 왕복 거래비용을 못 넘으면 사지 않는다(06-sizing 6.1)
        edge = costs.entry_edge(
            float(close), stop, qty, markets.get(o.code, "KOSPI"), day,
            reward_r=reward_r,
        )
        if edge["net_edge"] <= 0:
            no_trades[o.code] = edge
            continue

        value = float(close) * qty
        # 상태를 모르는 종목은 진입시키지 않는다 — 조회 실패를 정상으로 넘기지 않기 위함
        if quotes is None:
            status = _UNKNOWN_STATUS
        elif o.code in quotes:
            status = quotes[o.code].status
        else:
            checks.append(PendingCheck(
                o.code, "symbolState", "reject", "종목 상태 조회 실패(모름은 진입 불가)",
            ))
            continue

        verdict = screen_order(account, o.code, committed + value, status, params)
        if not verdict:
            checks.append(PendingCheck(
                o.code, verdict.check or "hardLimit", "reject", verdict.reason,
                limit_value=float(lim["gross_exposure_max"]) * equity,
                actual_value=committed + value,
            ))
            continue

        checks.append(PendingCheck(o.code, "symbolState", "pass"))
        committed += value
        slots -= 1
        planned.append(PlannedOrder(
            o.code, qty, float(close), float(stop), o.thesis,
            estimated_cost=edge["estimated_cost"], net_edge=edge["net_edge"],
            reward_risk_ratio=edge["reward_risk_ratio"],
        ))
    return planned, checks, no_trades


def _cycle_score_rows(
    wl: pd.DataFrame,
    pnl: pd.DataFrame,
    holdings: tuple[str, ...],
    quotes: dict[str, Quote] | None,
    params: dict,
) -> list[dict]:
    """워치리스트 각 종목의 사이클 시점 값을 CycleScores 행으로 만든다."""
    stop_k = float(params["entry"]["stop_atr_k"])
    held = set(holdings)
    rows: list[dict] = []
    for code in wl.index:
        row = pnl.loc[code] if code in pnl.index else None
        atr = _f(row.get("atr")) if row is not None else None
        q = (quotes or {}).get(code)
        # LastPrice는 사이클 시점 현재가. 시세 경로가 없으면 전일 종가로 대신한다
        last = q.last_price if q is not None else (
            _f(row.get("close")) if quotes is None and row is not None else None
        )
        block = q.status.block_reason if q is not None else ""
        score = _f(wl.loc[code, "score"])
        rows.append({
            "symbol_id": code,
            "inclusion": "holding" if code in held else "topRank",
            "base_score": score,
            "total_score": score,
            "last_price": last,
            "atr": atr,
            "stop_width": None if atr is None else stop_k * atr,
            # 상태를 모르면 판정 불가 — 정상(True)으로 적지 않는다
            "is_tradable": None if q is None else not block,
            "block_reason": block,
        })
    return rows


def _raise_safe_stop(conn, cycle_id: str, cause: str) -> str:
    """SafeStop을 적재하고 알림까지 보낸다. 반환: EventId."""
    event_id = journal.record_safe_stop(conn, cause=cause, cycle_id=cycle_id)
    notify.notify_safe_stop(cause, cycle_id)
    return event_id


def run(
    conn: psycopg.Connection,
    *,
    market_data: dict[str, pd.DataFrame] | None = None,
    holdings: tuple[str, ...] = (),
    asof: date | None = None,
    trade_date: date | None = None,
    account: Account | None = None,
    market_state: MarketState | None = None,
    quotes: dict[str, Quote] | None = None,
    quote_fetcher: Callable[[list[str]], dict[str, Quote]] | None = None,
    market_map: dict[str, str] | None = None,
    params: dict | None = None,
    mode: str | None = None,
    broker: Broker | None = None,
) -> CycleResult:
    """사이클 한 번 실행 — 선별→결정→리스크 게이트→집행까지 상태머신을 따라 진행한다.

    `asof`는 **지표 기준일**(전일 확정 일봉의 마지막 날)이고, `trade_date`는 사이클이
    도는 날이다. 둘을 나누는 이유는 설계 4.2다 — 점수·ATR은 전일 값으로 고정하고,
    현재가는 진입가·손절가 확정에만 쓴다.
    """
    cycle_id = new_cycle_id()
    run_mode = mode or get_settings().trading_mode
    p = params or load_params("risk_params")
    today = trade_date or kst_today()
    journal.create_cycle(conn, cycle_id, trade_date=today, mode=run_mode)

    # 현금 대조(검사 1-b) — 스냅샷보다 먼저 해야 기준선을 옮긴 값으로 기록된다.
    recon = CashReconciliation(actual=account.cash) if account is not None \
        else CashReconciliation()
    if account is not None:
        recon = _reconcile_cash(conn, account, mode=run_mode)
        # 기준선을 옮길 금액을 계좌에 실어 보낸다 — 서킷브레이커가 이걸 분모에 쓴다
        account = replace(account, net_external_flow=recon.net_external_flow)

    # 사이클 시점 자본 스냅샷 — 사이징 분모이자 대시보드 ①의 원천
    if account is not None:
        position_value = sum(pos.value for pos in account.positions)
        journal.record_account_snapshot(
            conn, cycle_id=cycle_id, cash=account.cash, position_value=position_value,
            total_asset=account.equity, base_asset=account.start_capital,
            net_flow_since_base=recon.net_external_flow,
            flow_this_snapshot=recon.net_external_flow,
            trade_date=today,
        )

    # 1단계: 후보 선별 → 워치리스트
    wl: pd.DataFrame | None = None
    pnl = pd.DataFrame()
    if market_data:
        wl, pnl = screening.run_screening(
            market_data, holdings=holdings, asof=asof, params=p,
        )
        watchlist = list(wl.index)
    else:
        watchlist = list(holdings)
    journal.advance_status(conn, cycle_id, "scoring")

    # 2단계: 워치리스트로 좁힌 뒤에야 종목별 실시간 조회가 가능해진다(04-data 4.2)
    if quotes is None and quote_fetcher is not None and watchlist:
        quotes = quote_fetcher(watchlist)

    # 2단계 산출물: 워치리스트 종목의 사이클 시점 값
    if wl is not None and not wl.empty:
        journal.record_cycle_scores(
            conn, cycle_id, _cycle_score_rows(wl, pnl, holdings, quotes, p)
        )

    decision: DeciderOutput | None = None
    planned: list[PlannedOrder] = []
    pending: list[PendingCheck] = []
    no_trades: dict[str, dict] = {}
    cycle_action = "proceed"
    blocked_reason = ""
    safe_stop_id: str | None = None

    # 3~4단계: 결정 + 사이클 리스크 게이트
    if account is not None and wl is not None and not wl.empty:
        journal.advance_status(conn, cycle_id, "deciding")
        ms = market_state or MarketState()
        # 호출부가 명시적으로 넣은 값이 있으면 존중하고, 없을 때만 대조 결과를 쓴다
        ms = replace(
            ms,
            cash_negative=ms.cash_negative or account.cash < 0,
            cash_residual=ms.cash_residual or recon.residual,
        )
        verdict = screen_cycle(ms, account, p)

        # 미해제 SafeStop이 있으면 신규 주문만 막는다(보유 청산은 계속 돈다 — 07-model 7.4)
        held_stop = journal.active_safe_stop(conn)
        if held_stop is not None and verdict.action == "proceed":
            verdict.action, verdict.result = "new_blocked", "reject"
            verdict.reason = f"미해제 SafeStop({held_stop['cause']})"
            verdict.check = "circuitBreaker"

        cycle_action, blocked_reason = verdict.action, verdict.reason
        journal.record_risk_check(
            conn, cycle_id=cycle_id, check_order=CHECK_ORDER[verdict.check],
            check_name=verdict.check, result=verdict.result, reason=verdict.reason,
        )
        if verdict.result == "safeStop" and held_stop is None:
            safe_stop_id = _raise_safe_stop(conn, cycle_id, verdict.reason)

        # 미수·대형 유출로 멈춘 게 아니라면 잔차를 외부 현금흐름으로 남기고 그대로 간다.
        # 기대 예수금 기준점 재동기화는 위 스냅샷이 실제 예수금을 그대로 적어서 이미 됐다 —
        # 흡수한 잔차까지 매번 재동기화해야 몇 원씩 쌓여 가짜 이체가 되는 일이 없다.
        if verdict.action != "halt":
            _record_cash_flow(conn, cycle_id, recon, mode=run_mode)

        if verdict.action in ("proceed", "new_blocked"):
            candidates = [
                Candidate(code, float(wl.loc[code, "score"])) for code in wl.index
            ]
            decision = run_decision(candidates, list(holdings), params=p)
            if verdict.action == "new_blocked":
                decision = _drop_new_entries(decision)
            planned, pending, no_trades = _plan_entries(
                decision, pnl, account, p, quotes, market_map, today,
            )
            proposals = [OrderProposal(o.code, "buy", o.value) for o in planned]
            anomaly = detect_anomaly(proposals, account, p)
            if not anomaly:
                # 코드 고장이므로 이 사이클 신규를 전부 버리고 전체 정지한다(05-risk 5.3)
                planned, cycle_action, blocked_reason = [], "halt", anomaly.reason
                safe_stop_id = _raise_safe_stop(conn, cycle_id, anomaly.reason)

    # 결정 의도를 먼저 기록 — 송출 전에 무엇을 하려 했는지 남긴다
    decision_ids: dict[str, str] = {}
    if decision is not None:
        journal.record_decisions(
            conn, cycle_id, decision.orders,
            plans={o.code: o for o in planned},
            no_trades=no_trades,
            entry_threshold=p["decision"].get("entry_threshold"),
            exit_threshold=p["decision"].get("exit_threshold"),
            target_positions=int(p["limits"]["max_positions"]),
        )
        for o in decision.orders:
            if o.action in (OrderAction.BUY, OrderAction.ADD) and o.code not in no_trades:
                decision_ids[o.code] = f"{cycle_id}_{o.code}_buy"

    # 결정 단위 게이트 판정 — Decisions가 들어간 뒤라야 외래키가 성립한다
    for c in pending:
        journal.record_risk_check(
            conn, cycle_id=cycle_id, check_order=CHECK_ORDER[c.check_name],
            check_name=c.check_name, result=c.result, reason=c.reason,
            decision_id=decision_ids.get(c.code),
            limit_value=c.limit_value, actual_value=c.actual_value,
        )

    # 게이트가 사이클을 끊었으면 여기서 끝낸다 — 주문은 한 건도 내지 않는다
    if cycle_action in ("skip", "halt"):
        journal.advance_status(
            conn, cycle_id,
            "skipped" if cycle_action == "skip" else "failed",
            failed_step=None if cycle_action == "skip" else 4,
            skip_reason=blocked_reason or None,
        )
        return CycleResult(
            cycle_id, watchlist, decision, [], cycle_action, blocked_reason,
            [], safe_stop_id,
        )

    journal.advance_status(conn, cycle_id, "ordering")
    # 5단계: 주문 송출 — broker 미주입이면 드라이런(집행 계획까지만)
    order_ids: list[str] = []
    if broker is not None:
        if market_data:
            forced_sells = (
                [o.code for o in decision.orders if o.action == OrderAction.SELL]
                if decision is not None else []
            )
            order_ids += exits.execute_exits(
                conn, market_data, broker=broker, cycle_id=cycle_id, asof=asof,
                trade_date=today, params=p,
                last_prices={
                    c: q.last_price for c, q in (quotes or {}).items()
                    if q.last_price is not None
                },
                order_mode=run_mode, mode=run_mode, forced_sells=forced_sells,
            )
        if planned:
            order_ids += orders.execute_entries(
                conn, planned, broker=broker, cycle_id=cycle_id,
                decision_ids=decision_ids, market_map=universe.load_market_map(),
                order_mode=run_mode, mode=run_mode,
            )

    journal.advance_status(conn, cycle_id, "recorded")
    return CycleResult(
        cycle_id, watchlist, decision, planned, cycle_action, blocked_reason,
        order_ids, safe_stop_id,
    )
