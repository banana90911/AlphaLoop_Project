"""사이클·결정·주문·보유·손익 적재 (07-model 7장 표 카탈로그).

표 이름과 컬럼 이름은 문서의 PascalCase 그대로다 — PostgreSQL은 큰따옴표로 감싸지
않은 식별자를 전부 소문자로 접기 때문에, 여기 SQL은 예외 없이 감싼다.

idempotency: 사이클은 `intent`→`scoring`→`deciding`→`ordering`→`recorded` 상태머신을
따르며, 미완으로 남은 사이클은 시작 시 복구한다(10-ops 10.1). 주문은 `ClientOrderId`가
기본키라, 재시작 뒤 같은 의도를 다시 송출하면 삽입 단계에서 거부된다.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

import psycopg

from core.schemas import ProposedOrder
from core.timeutils import kst_today, now_utc

CYCLE_STATES = ("intent", "scoring", "deciding", "ordering", "recorded", "failed", "skipped")
# 이 상태로 남았다면 프로세스가 사이클 도중 죽은 것이다(마감 상태가 아니다).
PENDING_STATES = ("intent", "scoring", "deciding", "ordering")

# OrderAction(buy/add/hold/trim/sell) → Decisions의 (Action, Reason).
# hold는 주문이 없어 적재하지 않는다. 부분 청산(exitPartial)은 규칙에서 뺐으므로(06-sizing 6.2)
# trim도 전량 청산으로 기록한다.
_ACTION_MAP: dict[str, tuple[str, str]] = {
    "buy":  ("buy", "entryThreshold"),      # 신규 진입
    "add":  ("buy", "entryThreshold"),      # 추가 매수
    "sell": ("exitAll", "thesisInvalid"),   # 전량 청산
    "trim": ("exitAll", "thesisInvalid"),   # 부분 청산 폐지 → 전량 청산
}


def create_cycle(
    conn: psycopg.Connection,
    cycle_id: str,
    *,
    trade_date: date | None = None,
    mode: str = "paper",
) -> None:
    """`intent` 상태로 사이클 1행 생성(모든 산출물의 부모 키)."""
    conn.execute(
        'INSERT INTO "Cycles"("CycleId", "TradeDate", "Status", "Mode", "StartedDateTime") '
        "VALUES(%s, %s, %s, %s, %s)",
        (cycle_id, trade_date or kst_today(), "intent", mode, now_utc()),
    )
    conn.commit()


def advance_status(
    conn: psycopg.Connection,
    cycle_id: str,
    status: str,
    *,
    failed_step: int | None = None,
    skip_reason: str | None = None,
) -> None:
    """상태 전이. 마감 상태(`recorded`/`failed`/`skipped`)면 FinishedDateTime을 찍는다."""
    if status not in CYCLE_STATES:
        raise ValueError(f"unknown cycle status: {status}")
    if status in ("recorded", "failed", "skipped"):
        conn.execute(
            'UPDATE "Cycles" SET "Status"=%s, "FinishedDateTime"=%s, '
            '"FailedStep"=COALESCE(%s, "FailedStep"), "SkipReason"=COALESCE(%s, "SkipReason") '
            'WHERE "CycleId"=%s',
            (status, now_utc(), failed_step, skip_reason, cycle_id),
        )
    else:
        conn.execute(
            'UPDATE "Cycles" SET "Status"=%s WHERE "CycleId"=%s', (status, cycle_id)
        )
    conn.commit()


def record_decisions(
    conn: psycopg.Connection,
    cycle_id: str,
    orders: Iterable[ProposedOrder],
    *,
    plans: dict[str, Any] | None = None,
    entry_threshold: float | None = None,
    exit_threshold: float | None = None,
    target_positions: int | None = None,
    decided_at: datetime | None = None,
) -> list[str]:
    """결정 제안을 `Decisions`에 적재. 반환: DecisionId 목록.

    hold는 주문이 없어 건너뛴다. plans는 code → 집행 계획(qty·price·stop 속성을 가진
    PlannedOrder)이며, 진입 결정의 수량·진입가·손절가를 여기서 채운다. target_positions는
    동일가중 배분의 분모(그 시점 목표 보유 종목 수)다. DecisionId는
    cycle_id+종목+Action의 결정론 키(사이클 내 유일)라 재시작해도 같은 값이 나온다.
    """
    plans = plans or {}
    ts = decided_at or now_utc()
    ids: list[str] = []
    for o in orders:
        mapped = _ACTION_MAP.get(str(o.action))
        if mapped is None:                           # hold 등은 적재 생략
            continue
        action, reason = mapped
        plan = plans.get(o.code)
        did = f"{cycle_id}_{o.code}_{action}"
        conn.execute(
            'INSERT INTO "Decisions"("DecisionId", "CycleId", "SymbolId", "Action", "Reason", '
            '"Score", "Threshold", "EntryPrice", "StopPrice", "RiskPerShare", '
            '"TargetPositions", "Quantity", "DecidedDateTime") '
            "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                did, cycle_id, o.code, action, reason, o.risk_budget,
                entry_threshold if action == "buy" else exit_threshold,
                getattr(plan, "price", None),
                getattr(plan, "stop", None),
                _risk_per_share(plan),
                target_positions,
                getattr(plan, "qty", None),
                ts,
            ),
        )
        ids.append(did)
    conn.commit()
    return ids


def _risk_per_share(plan: Any) -> float | None:
    """R = 진입가 − 최초 손절가. 집행 계획이 없으면(청산 결정 등) NULL."""
    price, stop = getattr(plan, "price", None), getattr(plan, "stop", None)
    return float(price) - float(stop) if price is not None and stop is not None else None


def record_order(
    conn: psycopg.Connection,
    *,
    client_order_id: str,
    cycle_id: str | None,
    decision_id: str | None,
    symbol_id: str,
    side: str,
    purpose: str,
    order_type: str,
    order_quantity: int,
    filled_quantity: int,
    status: str,
    order_price: float | None = None,
    trigger_price: float | None = None,
    average_fill_price: float | None = None,
    kis_order_no: str | None = None,
    fee: float | None = None,
    tax: float | None = None,
    slippage_estimate: float | None = None,
    mode: str = "paper",
    ordered_at: datetime | None = None,
    filled_at: datetime | None = None,
) -> None:
    """KIS 주문·체결 1건을 `Orders`에 적재. status∈submitted/partial/filled/cancelled/rejected.

    체결가·수량은 broker가 정규화한 Fill 기준. cycle_id·decision_id는 상주 스톱 자동 체결
    시 NULL이 될 수 있어, 모드는 `Mode` 열에 따로 남긴다(07-model).
    """
    conn.execute(
        'INSERT INTO "Orders"("ClientOrderId", "CycleId", "DecisionId", "KisOrderNo", '
        '"SymbolId", "Side", "Purpose", "OrderType", "OrderQuantity", "OrderPrice", '
        '"TriggerPrice", "FilledQuantity", "AverageFillPrice", "Fee", "Tax", '
        '"SlippageEstimate", "Status", "OrderedDateTime", "FilledDateTime", "Mode") '
        "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (client_order_id, cycle_id, decision_id, kis_order_no, symbol_id, side, purpose,
         order_type, order_quantity, order_price, trigger_price, filled_quantity,
         average_fill_price, fee, tax, slippage_estimate, status,
         ordered_at or now_utc(), filled_at, mode),
    )
    conn.commit()


def upsert_entry_position(
    conn: psycopg.Connection,
    *,
    cycle_id: str,
    symbol_id: str,
    add_quantity: int,
    fill_price: float,
    entry_decision_id: str | None,
    current_stop_price: float | None,
    initial_stop_price: float | None = None,
    market: str | None = None,
    entry_date: date | None = None,
) -> str:
    """진입 체결 → `Positions` 생성 또는 수량·평단 갱신. 반환: PositionId.

    같은 종목 open 보유가 있으면 수량 합산·평단 가중평균으로 갱신(추가매수), 없으면 신규
    생성(PositionId = cycle_id_종목). 추가매수 시 R 기준(InitialStopPrice·EntryDate)은 첫
    진입값을 유지한다 — R은 청산까지 불변이기 때문이다.
    """
    now = now_utc()
    row = conn.execute(
        'SELECT "PositionId", "Quantity", "AveragePrice" FROM "Positions" '
        "WHERE \"SymbolId\"=%s AND \"Status\"='open'",
        (symbol_id,),
    ).fetchone()
    if row is not None:
        pid, q0, p0 = row["PositionId"], row["Quantity"], row["AveragePrice"]
        new_qty = q0 + add_quantity
        new_avg = (q0 * p0 + add_quantity * fill_price) / new_qty if new_qty else fill_price
        conn.execute(
            'UPDATE "Positions" SET "Quantity"=%s, "AveragePrice"=%s, "CurrentStopPrice"=%s, '
            '"UpdatedDateTime"=%s WHERE "PositionId"=%s',
            (new_qty, new_avg, current_stop_price, now, pid),
        )
    else:
        pid = f"{cycle_id}_{symbol_id}"
        initial = initial_stop_price if initial_stop_price is not None else current_stop_price
        conn.execute(
            'INSERT INTO "Positions"("PositionId", "SymbolId", "Market", "Quantity", '
            '"AveragePrice", "EntryDecisionId", "EntryDate", "InitialStopPrice", '
            '"CurrentStopPrice", "RiskPerShare", "IsBreakevenDone", "Status", '
            '"OpenedDateTime", "UpdatedDateTime") '
            "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, 'open', %s, %s)",
            (pid, symbol_id, market, add_quantity, fill_price, entry_decision_id,
             entry_date or kst_today(), initial, current_stop_price,
             fill_price - initial if initial is not None else None, now, now),
        )
    conn.commit()
    return pid


def set_active_stop(conn: psycopg.Connection, position_id: str, client_order_id: str) -> None:
    """KIS에 상주 중인 스톱 주문을 포지션에 연결 — 비어 있으면 손절 없이 방치된 포지션이다."""
    conn.execute(
        'UPDATE "Positions" SET "ActiveStopOrderId"=%s, "UpdatedDateTime"=%s '
        'WHERE "PositionId"=%s',
        (client_order_id, now_utc(), position_id),
    )
    conn.commit()


def update_stop(
    conn: psycopg.Connection,
    position_id: str,
    new_stop: float,
    *,
    breakeven_done: bool | None = None,
) -> None:
    """트레일링·본전 상향 — CurrentStopPrice 갱신(청산 없음, exits ③④)."""
    conn.execute(
        'UPDATE "Positions" SET "CurrentStopPrice"=%s, '
        '"IsBreakevenDone"=COALESCE(%s, "IsBreakevenDone"), "UpdatedDateTime"=%s '
        'WHERE "PositionId"=%s',
        (new_stop, breakeven_done, now_utc(), position_id),
    )
    conn.commit()


def reduce_position(
    conn: psycopg.Connection,
    position_id: str,
    *,
    sell_quantity: int,
    new_stop: float | None = None,
) -> None:
    """부분 체결 뒤처리 — 체결분만 수량 차감(+선택 손절 갱신). 부분 익절은 설계에 없다."""
    conn.execute(
        'UPDATE "Positions" SET "Quantity"=GREATEST(0, "Quantity" - %s), '
        '"CurrentStopPrice"=COALESCE(%s, "CurrentStopPrice"), "UpdatedDateTime"=%s '
        'WHERE "PositionId"=%s',
        (sell_quantity, new_stop, now_utc(), position_id),
    )
    conn.commit()


def close_position(conn: psycopg.Connection, position_id: str) -> None:
    """전량 청산 — Status=closed, 잔량 0(exits ①·②·⑤)."""
    conn.execute(
        'UPDATE "Positions" SET "Status"=\'closed\', "Quantity"=0, "UpdatedDateTime"=%s '
        'WHERE "PositionId"=%s',
        (now_utc(), position_id),
    )
    conn.commit()


def record_outcome(
    conn: psycopg.Connection,
    *,
    outcome_id: str,
    position_id: str,
    symbol_id: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    holding_days: int,
    gross_profit_loss: float,
    net_profit_loss: float,
    return_percent: float,
    exit_reason: str,
    exit_kind: str = "full",
    entry_decision_id: str | None = None,
    exit_decision_id: str | None = None,
    entry_date: date | None = None,
    exit_date: date | None = None,
    fee: float | None = None,
    tax: float | None = None,
    r_multiple: float | None = None,
    entry_score: float | None = None,
    entry_regime: str | None = None,
    mode: str = "paper",
    closed_at: datetime | None = None,
) -> None:
    """청산 체결 1건의 실현손익을 `Outcomes`에 적재. NetProfitLoss는 비용 차감 후.

    성과 집계의 1차 자료 — 산식은 백테스트 `spec_engine`의 청산 처리와 동일하게
    `core.costs.trade_cost` 기반이다(모드가 달라도 같은 경로).
    """
    conn.execute(
        'INSERT INTO "Outcomes"("OutcomeId", "PositionId", "EntryDecisionId", '
        '"ExitDecisionId", "SymbolId", "EntryPrice", "ExitPrice", "Quantity", "EntryDate", '
        '"ExitDate", "HoldingDays", "GrossProfitLoss", "Fee", "Tax", "NetProfitLoss", '
        '"ReturnPercent", "RMultiple", "ExitKind", "ExitReason", "EntryScore", '
        '"EntryRegime", "ClosedDateTime", "Mode") '
        "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s, %s, %s)",
        (outcome_id, position_id, entry_decision_id, exit_decision_id, symbol_id,
         entry_price, exit_price, quantity, entry_date, exit_date, holding_days,
         gross_profit_loss, fee, tax, net_profit_loss, return_percent, r_multiple,
         exit_kind, exit_reason, entry_score, entry_regime,
         closed_at or now_utc(), mode),
    )
    conn.commit()


def recover_pending_cycles(conn: psycopg.Connection) -> list[str]:
    """시작 시 미완 사이클을 failed로 마감하고 그 id 목록 반환(10-ops 10.1).

    프로세스가 사이클 도중 죽어도 다음 실행이 깨끗한 상태에서 시작하게 한다.
    """
    rows = conn.execute(
        'SELECT "CycleId" FROM "Cycles" WHERE "Status" = ANY(%s)', (list(PENDING_STATES),)
    ).fetchall()
    pending = [r["CycleId"] for r in rows]
    for cid in pending:
        advance_status(conn, cid, "failed")
    return pending
