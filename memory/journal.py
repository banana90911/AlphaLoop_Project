"""
description:        사이클·결정·주문·보유·손익 적재 (PostgreSQL 기록 계층)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

import psycopg

from core.schemas import ProposedOrder
from core.timeutils import kst_today, now_utc

CYCLE_STATES = ("intent", "scoring", "deciding", "ordering", "recorded", "failed", "skipped")
PENDING_STATES = ("intent", "scoring", "deciding", "ordering")  # 미완 = 프로세스가 도중 죽은 상태

# OrderAction → Decisions의 (Action, Reason). hold는 적재하지 않고, trim은 전량 청산으로 기록한다.
_ACTION_MAP: dict[str, tuple[str, str]] = {
    "buy":  ("buy", "entryThreshold"),
    "add":  ("buy", "entryThreshold"),
    "sell": ("exitAll", "thesisInvalid"),
    "trim": ("exitAll", "thesisInvalid"),
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


def last_account_snapshot(conn: psycopg.Connection) -> dict[str, Any] | None:
    """가장 최근 계좌 스냅샷 1행(없으면 None). 기준선·누적값을 이어받는 출발점."""
    row = conn.execute(
        'SELECT * FROM "AccountSnapshots" ORDER BY "RecordedDateTime" DESC LIMIT 1'
    ).fetchone()
    return dict(row) if row else None


def record_account_snapshot(
    conn: psycopg.Connection,
    *,
    cycle_id: str,
    cash: float,
    position_value: float,
    total_asset: float,
    base_asset: float | None = None,
    net_flow_since_base: float = 0.0,
    flow_this_snapshot: float = 0.0,
    trade_date: date | None = None,
) -> str:
    """사이클 시점 자본을 `AccountSnapshots`에 남긴다. 반환: SnapshotId.

    `base_asset`(서킷브레이커 기준선)은 **직전 거래일 마지막 스냅샷의 TotalAsset**이고,
    분모는 거기에 그 사이 순외부흐름을 더한 `AdjustedBaseAsset`이다 — 이체는 손익이
    아니므로 기준선을 같이 밀어줘야 손익률이 진실을 말한다(05-risk 5.2).

    `flow_this_snapshot`은 **직전 스냅샷 이후** 새로 감지된 순외부흐름이다.
    누적 순입금(`CumulativeNetFlow`)과 TWR 지수는 직전 행에서 이어받아 갱신한다.
    """
    prev = last_account_snapshot(conn)

    if base_asset is None:
        base_asset = float(prev["TotalAsset"]) if prev else total_asset
    adjusted = base_asset + net_flow_since_base
    day_return = total_asset / adjusted - 1.0 if adjusted else None

    prev_cum = float(prev["CumulativeNetFlow"]) if prev else 0.0
    cumulative = prev_cum + flow_this_snapshot

    # TWR 구간수익률 — 기초자산에 이번 구간의 흐름을 얹은 값이 분모다.
    # 흐름을 빼지 않으면 입금이 그대로 "수익"으로 잡힌다(09-eval).
    prev_index = float(prev["TwrIndex"]) if prev and prev["TwrIndex"] is not None else 1.0
    prev_total = float(prev["TotalAsset"]) if prev else None
    if prev_total is None:
        twr_index = 1.0
    else:
        denom = prev_total + flow_this_snapshot
        twr_index = (
            prev_index * (total_asset / denom) if denom > 0 else prev_index
        )

    sid = f"{cycle_id}_snap"
    conn.execute(
        'INSERT INTO "AccountSnapshots"("SnapshotId", "CycleId", "TradeDate", "Amount", '
        '"PositionValue", "TotalAsset", "BaseAsset", "NetFlowSinceBase", '
        '"AdjustedBaseAsset", "CumulativeNetFlow", "TwrIndex", "DayReturnPercent", '
        '"RecordedDateTime") VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (sid, cycle_id, trade_date or kst_today(), cash, position_value, total_asset,
         base_asset, net_flow_since_base, adjusted, cumulative, twr_index,
         day_return, now_utc()),
    )
    conn.commit()
    return sid


def record_cycle_scores(
    conn: psycopg.Connection, cycle_id: str, rows: Iterable[dict[str, Any]]
) -> int:
    """워치리스트 종목의 사이클 시점 값을 `CycleScores`에 적재. 반환: 적재 행 수."""
    now = now_utc()
    n = 0
    for r in rows:
        conn.execute(
            'INSERT INTO "CycleScores"("CycleId", "SymbolId", "Inclusion", "BaseScore", '
            '"FlowPercentileLive", "TotalScore", "LastPrice", "BuyQuantity", '
            '"SellQuantity", "Atr", "StopWidth", "IsTradable", "BlockReason", '
            '"ScoredDateTime") '
            "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            'ON CONFLICT ("CycleId", "SymbolId") DO NOTHING',
            (cycle_id, r["symbol_id"], r.get("inclusion", "topRank"),
             r.get("base_score"), r.get("flow_percentile_live"), r.get("total_score"),
             r.get("last_price"), r.get("buy_quantity"), r.get("sell_quantity"),
             r.get("atr"), r.get("stop_width"), r.get("is_tradable"),
             r.get("block_reason") or None, now),
        )
        n += 1
    conn.commit()
    return n


def record_risk_check(
    conn: psycopg.Connection,
    *,
    cycle_id: str,
    check_order: int,
    check_name: str,
    result: str,
    decision_id: str | None = None,
    reason: str | None = None,
    limit_value: float | None = None,
    actual_value: float | None = None,
) -> str:
    """게이트 판정 1건을 `RiskChecks`에 남긴다. 반환: CheckId."""
    suffix = decision_id or "cycle"
    check_id = f"{cycle_id}_{suffix}_{check_name}"
    conn.execute(
        'INSERT INTO "RiskChecks"("CheckId", "CycleId", "DecisionId", "CheckOrder", '
        '"CheckName", "Result", "Reason", "LimitValue", "ActualValue", "CheckedDateTime") '
        "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        'ON CONFLICT ("CheckId") DO NOTHING',
        (check_id, cycle_id, decision_id, check_order, check_name, result,
         reason or None, limit_value, actual_value, now_utc()),
    )
    conn.commit()
    return check_id


# ── 외부 현금흐름(CashFlows) ────────────────────────────────────────────────
# 주식은 맞는데 예수금만 어긋난 잔차. Kind가 회계적으로 의미를 갖는다 —
# deposit/withdrawal은 수익률에서 제거할 외부 흐름, dividend/taxRefund/interest는
# 수익이라 제거하면 안 된다(09-eval). unknown은 보수적으로 외부 흐름 취급.
FLOW_KINDS = ("deposit", "withdrawal", "dividend", "taxRefund", "interest", "fee", "unknown")
EXTERNAL_KINDS = ("deposit", "withdrawal", "unknown")   # TWR에서 제거하는 것들
FLOW_STATUSES = ("unconfirmed", "confirmed", "reclassified")
FLOW_SOURCES = ("residual", "signature", "broker", "manual")


def expected_cash(conn: psycopg.Connection, *, mode: str | None = None) -> dict[str, Any] | None:
    """직전 스냅샷 이후 체결로만 설명되는 **기대 예수금**을 계산한다(05-risk 5.2 검사 1-b).

        기대 예수금 = 직전 스냅샷 예수금
                    + Σ(그 사이 매도 체결 순수취금)
                    − Σ(그 사이 매수 체결 순지급금)

    직전 스냅샷이 없으면(첫 사이클) None — 비교할 기준이 없으니 잔차도 없다.
    미체결 매수 주문이 증거금으로 묶일 걱정은 없다: 선행 게이트가 사이클 시작 때
    먼저 취소하므로 이 시점에 살아 있는 미체결 진입 주문이 없다.
    """
    prev = last_account_snapshot(conn)
    if prev is None:
        return None
    sql = (
        'SELECT "Side", COALESCE(SUM("FilledQuantity" * "AverageFillPrice"), 0) AS gross, '
        'COALESCE(SUM(COALESCE("Fee", 0) + COALESCE("Tax", 0)), 0) AS charges '
        'FROM "Orders" WHERE "FilledQuantity" > 0 AND "AverageFillPrice" IS NOT NULL '
        'AND "FilledDateTime" > %s'
    )
    args: list[Any] = [prev["RecordedDateTime"]]
    if mode:
        sql += ' AND "Mode" = %s'
        args.append(mode)
    rows = conn.execute(sql + ' GROUP BY "Side"', args).fetchall()

    delta = 0.0
    for r in rows:
        gross, charges = float(r["gross"] or 0), float(r["charges"] or 0)
        # 매도는 세금·수수료를 뗀 만큼 들어오고, 매수는 수수료를 얹은 만큼 나간다.
        delta += (gross - charges) if r["Side"] == "sell" else -(gross + charges)

    return {
        "expected": float(prev["Amount"]) + delta,
        "prev_cash": float(prev["Amount"]),
        "fills_delta": delta,
        "since": prev["RecordedDateTime"],
        "prev_total_asset": float(prev["TotalAsset"]),
        "prev_snapshot_id": prev["SnapshotId"],
    }


def record_cash_flow(
    conn: psycopg.Connection,
    cycle_id: str | None,
    *,
    kind: str,
    amount: float,
    source: str,
    expected: float,
    actual: float,
    mode: str = "paper",
    status: str = "unconfirmed",
    note: str | None = None,
    trade_date: date | None = None,
) -> str:
    """외부 현금흐름 1건을 `CashFlows`에 남긴다. 반환: FlowId.

    `amount`는 부호가 있다(유입 +, 유출 −). `expected`/`actual`은 감지 시점의
    기대·실제 예수금으로, 나중에 이 판정이 옳았는지 되짚는 유일한 근거다.
    """
    if kind not in FLOW_KINDS:
        raise ValueError(f"알 수 없는 Kind: {kind!r} (가능: {', '.join(FLOW_KINDS)})")
    if source not in FLOW_SOURCES:
        raise ValueError(f"알 수 없는 Source: {source!r}")
    now = now_utc()
    flow_id = f"F{now:%Y%m%dT%H%M%S%f}"
    conn.execute(
        'INSERT INTO "CashFlows"("FlowId", "DetectedCycleId", "TradeDate", "Kind", '
        '"Amount", "Status", "Source", "ExpectedCash", "ActualCash", "Note", '
        '"DetectedDateTime", "Mode") VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (flow_id, cycle_id, trade_date or kst_today(), kind, amount, status, source,
         expected, actual, note, now, mode),
    )
    conn.commit()
    return flow_id


def confirm_cash_flow(
    conn: psycopg.Connection,
    flow_id: str,
    *,
    kind: str,
    by: str = "cli",
    note: str | None = None,
) -> bool:
    """감지된 흐름에 사람이 라벨을 확정한다(CLI가 호출). 반환: 갱신 여부.

    이미 confirmed인 건을 다시 부르면 `reclassified`가 된다 — 배당을 입금으로
    잘못 잡았다가 바로잡는 경우가 이쪽이다.
    """
    if kind not in FLOW_KINDS:
        raise ValueError(f"알 수 없는 Kind: {kind!r} (가능: {', '.join(FLOW_KINDS)})")
    cur = conn.execute('SELECT "Status" FROM "CashFlows" WHERE "FlowId"=%s', (flow_id,))
    row = cur.fetchone()
    if row is None:
        return False
    status = "reclassified" if row["Status"] != "unconfirmed" else "confirmed"
    conn.execute(
        'UPDATE "CashFlows" SET "Kind"=%s, "Status"=%s, "ConfirmedDateTime"=%s, '
        '"ConfirmedBy"=%s, "Note"=COALESCE(%s, "Note") WHERE "FlowId"=%s',
        (kind, status, now_utc(), by, note, flow_id),
    )
    conn.commit()
    return True


def load_cash_flows(
    conn: psycopg.Connection,
    *,
    status: str | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """`CashFlows`를 최신순으로 읽는다(대시보드·CLI 공용)."""
    sql = 'SELECT * FROM "CashFlows" WHERE 1=1'
    args: list[Any] = []
    if status:
        sql += ' AND "Status"=%s'
        args.append(status)
    if start:
        sql += ' AND "TradeDate" >= %s'
        args.append(start)
    if end:
        sql += ' AND "TradeDate" <= %s'
        args.append(end)
    args.append(limit)
    rows = conn.execute(sql + ' ORDER BY "DetectedDateTime" DESC LIMIT %s', args).fetchall()
    return [dict(r) for r in rows]


def sum_flows_since(
    conn: psycopg.Connection, since: datetime | None, *, mode: str | None = None
) -> float:
    """`since` 이후 감지된 순외부흐름 합(입금 +, 출금 −).

    `NetFlowSinceBase`(서킷브레이커 기준선 평행이동 폭)를 만드는 데 쓴다.
    수익으로 분류된 흐름(배당·이자·환급)은 기준선을 옮기지 않으므로 제외한다 —
    그건 진짜 손익이라 손익률에 그대로 잡혀야 한다.
    """
    marks = ", ".join(["%s"] * len(EXTERNAL_KINDS))
    sql = f'SELECT COALESCE(SUM("Amount"), 0) AS s FROM "CashFlows" WHERE "Kind" IN ({marks})'
    args: list[Any] = list(EXTERNAL_KINDS)
    if since is not None:
        sql += ' AND "DetectedDateTime" > %s'
        args.append(since)
    if mode:
        sql += ' AND "Mode"=%s'
        args.append(mode)
    return float(conn.execute(sql, args).fetchone()["s"] or 0.0)


def cumulative_net_flow(conn: psycopg.Connection, *, mode: str | None = None) -> float:
    """개시 이후 누적 순입금. `TotalAsset − 이 값 = 누적 순손익`으로 검산된다."""
    return sum_flows_since(conn, None, mode=mode)


def record_safe_stop(
    conn: psycopg.Connection,
    *,
    cause: str,
    cycle_id: str | None = None,
    trigger: str = "auto",
) -> str:
    """전체 정지 발생을 `SafeStopEvents`에 남긴다. 반환: EventId."""
    now = now_utc()
    event_id = f"{cycle_id or 'manual'}_{now:%Y%m%dT%H%M%S%fZ}"
    conn.execute(
        'INSERT INTO "SafeStopEvents"("EventId", "CycleId", "OccurredDateTime", '
        '"Cause", "Trigger") VALUES(%s, %s, %s, %s, %s)',
        (event_id, cycle_id, now, cause, trigger),
    )
    conn.commit()
    return event_id


def active_safe_stop(conn: psycopg.Connection) -> dict[str, Any] | None:
    """미해제 SafeStop 중 가장 최근 것을 반환한다(없으면 None = 정상)."""
    return conn.execute(
        'SELECT * FROM "SafeStopEvents" WHERE "ReleasedDateTime" IS NULL '
        'ORDER BY "OccurredDateTime" DESC LIMIT 1'
    ).fetchone()


def release_safe_stop(
    conn: psycopg.Connection, event_id: str, *, released_by: str, reason: str
) -> None:
    """SafeStop을 해제한다(잔고 불일치·데이터 오류·이상행동은 사람 개입 필수 — 05-risk 5.4)."""
    conn.execute(
        'UPDATE "SafeStopEvents" SET "ReleasedDateTime"=%s, "ReleasedBy"=%s, '
        '"ReleaseReason"=%s WHERE "EventId"=%s AND "ReleasedDateTime" IS NULL',
        (now_utc(), released_by, reason, event_id),
    )
    conn.commit()


def record_decisions(
    conn: psycopg.Connection,
    cycle_id: str,
    orders: Iterable[ProposedOrder],
    *,
    plans: dict[str, Any] | None = None,
    no_trades: dict[str, dict[str, float]] | None = None,
    entry_threshold: float | None = None,
    exit_threshold: float | None = None,
    target_positions: int | None = None,
    decided_at: datetime | None = None,
) -> list[str]:
    """결정 제안을 `Decisions`에 적재(hold는 건너뜀). 반환: DecisionId 목록.

    `no_trades`에 든 종목은 매수 제안이었더라도 `noTrade`/`costExceedsEdge`로 남긴다 —
    기대이익이 왕복 비용을 못 넘어 사지 않기로 한 거래다(06-sizing 6.1).
    """
    plans = plans or {}
    no_trades = no_trades or {}
    ts = decided_at or now_utc()
    ids: list[str] = []
    for o in orders:
        mapped = _ACTION_MAP.get(str(o.action))
        if mapped is None:
            continue
        action, reason = mapped
        edge = no_trades.get(o.code)
        if edge is not None and action == "buy":
            action, reason = "noTrade", "costExceedsEdge"
        else:
            edge = _plan_edge(plans.get(o.code))
        plan = plans.get(o.code)
        did = f"{cycle_id}_{o.code}_{action}"
        conn.execute(
            'INSERT INTO "Decisions"("DecisionId", "CycleId", "SymbolId", "Action", "Reason", '
            '"Score", "Threshold", "EntryPrice", "StopPrice", "RiskPerShare", '
            '"TargetPositions", "Quantity", "RewardRiskRatio", "EstimatedCost", '
            '"NetEdge", "DecidedDateTime") '
            "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                did, cycle_id, o.code, action, reason, o.risk_budget,
                entry_threshold if action in ("buy", "noTrade") else exit_threshold,
                getattr(plan, "price", None),
                getattr(plan, "stop", None),
                _risk_per_share(plan),
                target_positions,
                getattr(plan, "qty", None),
                (edge or {}).get("reward_risk_ratio"),
                (edge or {}).get("estimated_cost"),
                (edge or {}).get("net_edge"),
                ts,
            ),
        )
        ids.append(did)
    conn.commit()
    return ids


def _plan_edge(plan: Any) -> dict[str, float] | None:
    """집행 계획이 들고 있는 엣지 값을 뽑는다(없으면 None)."""
    if plan is None:
        return None
    cost = getattr(plan, "estimated_cost", None)
    if cost is None:
        return None
    return {
        "estimated_cost": cost,
        "net_edge": getattr(plan, "net_edge", None),
        "reward_risk_ratio": getattr(plan, "reward_risk_ratio", None),
    }


def _risk_per_share(plan: Any) -> float | None:
    """R = 진입가 − 최초 손절가. 집행 계획이 없으면 NULL."""
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
    """KIS 주문·체결 1건을 `Orders`에 적재."""
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
    """진입 체결 → `Positions` 생성 또는 수량·평단 갱신(추가매수 병합). 반환: PositionId."""
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
    """KIS에 상주 중인 스톱 주문을 포지션에 연결한다."""
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
    """트레일링·본전 상향 — CurrentStopPrice 갱신(청산 없음)."""
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
    """부분 체결 뒤처리 — 체결분만 수량 차감(+선택 손절 갱신)."""
    conn.execute(
        'UPDATE "Positions" SET "Quantity"=GREATEST(0, "Quantity" - %s), '
        '"CurrentStopPrice"=COALESCE(%s, "CurrentStopPrice"), "UpdatedDateTime"=%s '
        'WHERE "PositionId"=%s',
        (sell_quantity, new_stop, now_utc(), position_id),
    )
    conn.commit()


def close_position(conn: psycopg.Connection, position_id: str) -> None:
    """전량 청산 — Status=closed, 잔량 0."""
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
    """청산 체결 1건의 실현손익을 `Outcomes`에 적재."""
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
    """시작 시 미완 사이클을 failed로 마감하고 그 id 목록을 반환한다."""
    rows = conn.execute(
        'SELECT "CycleId" FROM "Cycles" WHERE "Status" = ANY(%s)', (list(PENDING_STATES),)
    ).fetchall()
    pending = [r["CycleId"] for r in rows]
    for cid in pending:
        advance_status(conn, cid, "failed")
    return pending


# ── 일일 배치 — 시장 데이터 적재·조회(run_daily_ingest가 쓴다, 전부 upsert) ──────

def upsert_symbols(conn: psycopg.Connection, rows: Iterable[dict[str, Any]]) -> int:
    """종목 명부 적재(upsert). 반환: 적재 행 수."""
    now = now_utc()
    n = 0
    for r in rows:
        conn.execute(
            'INSERT INTO "Symbols"("SymbolId", "Name", "Market", "SecurityType", '
            '"LastUpdateDateTime") VALUES(%s, %s, %s, %s, %s) '
            'ON CONFLICT ("SymbolId") DO UPDATE SET '
            '"Name"=EXCLUDED."Name", "Market"=EXCLUDED."Market", '
            '"SecurityType"=EXCLUDED."SecurityType", '
            '"LastUpdateDateTime"=EXCLUDED."LastUpdateDateTime"',
            (r["code"], r["name"], r["market"], r.get("security_type", "common"), now),
        )
        n += 1
    conn.commit()
    return n


def upsert_daily_bars(
    conn: psycopg.Connection, symbol_id: str, bars: Iterable[dict[str, Any]]
) -> int:
    """일봉 적재(upsert). 거래대금이 없으면 종가×거래량으로 채운다. 반환: 적재 행 수."""
    n = 0
    for b in bars:
        close, volume = b.get("close"), b.get("volume")
        value = b.get("value")
        if value is None and close is not None and volume is not None:
            value = float(close) * float(volume)
        conn.execute(
            'INSERT INTO "DailyBars"("SymbolId", "TradeDate", "Open", "High", "Low", '
            '"Close", "Volume", "Value") VALUES(%s, %s, %s, %s, %s, %s, %s, %s) '
            'ON CONFLICT ("SymbolId", "TradeDate") DO UPDATE SET '
            '"Open"=EXCLUDED."Open", "High"=EXCLUDED."High", "Low"=EXCLUDED."Low", '
            '"Close"=EXCLUDED."Close", "Volume"=EXCLUDED."Volume", '
            '"Value"=EXCLUDED."Value"',
            (symbol_id, b["date"], b.get("open"), b.get("high"), b.get("low"),
             close, int(volume) if volume is not None else None, value),
        )
        n += 1
    conn.commit()
    return n


def upsert_daily_flows(
    conn: psycopg.Connection, symbol_id: str, flows: Iterable[dict[str, Any]]
) -> int:
    """수급 적재(upsert). 당일분은 IsFinal=false로 넣고 다음날 확정치로 덮는다."""
    n = 0
    today = kst_today()
    for f in flows:
        conn.execute(
            'INSERT INTO "DailyFlows"("SymbolId", "TradeDate", "ForeignNet", '
            '"InstitutionNet", "IsFinal", "CollectedDateTime") '
            "VALUES(%s, %s, %s, %s, %s, %s) "
            'ON CONFLICT ("SymbolId", "TradeDate") DO UPDATE SET '
            '"ForeignNet"=EXCLUDED."ForeignNet", '
            '"InstitutionNet"=EXCLUDED."InstitutionNet", '
            '"IsFinal"=EXCLUDED."IsFinal", '
            '"CollectedDateTime"=EXCLUDED."CollectedDateTime"',
            (symbol_id, f["date"], f.get("foreign_net"), f.get("inst_net"),
             f["date"] < today, now_utc()),
        )
        n += 1
    conn.commit()
    return n


def upsert_market_index(
    conn: psycopg.Connection, index_code: str, rows: Iterable[dict[str, Any]]
) -> int:
    """지수 적재(upsert). 반환: 적재 행 수."""
    now = now_utc()
    n = 0
    for r in rows:
        conn.execute(
            'INSERT INTO "MarketIndices"("IndexCode", "TradeDate", "Close", "Sma200", '
            '"Regime", "CollectedDateTime") VALUES(%s, %s, %s, %s, %s, %s) '
            'ON CONFLICT ("IndexCode", "TradeDate") DO UPDATE SET '
            '"Close"=EXCLUDED."Close", "Sma200"=EXCLUDED."Sma200", '
            '"Regime"=EXCLUDED."Regime", '
            '"CollectedDateTime"=EXCLUDED."CollectedDateTime"',
            (index_code, r["date"], r["close"], r.get("sma200"), r.get("regime"), now),
        )
        n += 1
    conn.commit()
    return n


def upsert_daily_scores(
    conn: psycopg.Connection, trade_date: date, rows: Iterable[dict[str, Any]]
) -> int:
    """전 종목 점수 적재(upsert, 원시값+백분위). 반환: 적재 행 수."""
    now = now_utc()
    n = 0
    for r in rows:
        conn.execute(
            'INSERT INTO "DailyScores"("TradeDate", "SymbolId", "PassedFilter", '
            '"FilterReason", "Momentum", "FlowNet20Day", "ValueRatio", "Volatility", '
            '"MomentumPercentile", "FlowPercentile", "ValuePercentile", '
            '"LowVolatilityPercentile", "TotalScore", "Rank", "ComputedDateTime") '
            "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            'ON CONFLICT ("TradeDate", "SymbolId") DO UPDATE SET '
            '"PassedFilter"=EXCLUDED."PassedFilter", '
            '"FilterReason"=EXCLUDED."FilterReason", "Momentum"=EXCLUDED."Momentum", '
            '"FlowNet20Day"=EXCLUDED."FlowNet20Day", "ValueRatio"=EXCLUDED."ValueRatio", '
            '"Volatility"=EXCLUDED."Volatility", '
            '"MomentumPercentile"=EXCLUDED."MomentumPercentile", '
            '"FlowPercentile"=EXCLUDED."FlowPercentile", '
            '"ValuePercentile"=EXCLUDED."ValuePercentile", '
            '"LowVolatilityPercentile"=EXCLUDED."LowVolatilityPercentile", '
            '"TotalScore"=EXCLUDED."TotalScore", "Rank"=EXCLUDED."Rank", '
            '"ComputedDateTime"=EXCLUDED."ComputedDateTime"',
            (trade_date, r["symbol_id"], r["passed_filter"], r.get("filter_reason"),
             r.get("momentum"), r.get("flow_net_20day"), r.get("value_ratio"),
             r.get("volatility"), r.get("momentum_percentile"),
             r.get("flow_percentile"), r.get("value_percentile"),
             r.get("low_volatility_percentile"), r.get("total_score"), r.get("rank"),
             now),
        )
        n += 1
    conn.commit()
    return n


def record_ingest_run(
    conn: psycopg.Connection,
    *,
    run_id: str,
    target_table: str,
    source: str,
    status: str,
    started_at: datetime,
    range_start: date | None = None,
    range_end: date | None = None,
    target_count: int | None = None,
    success_count: int | None = None,
    rows_written: int | None = None,
    error_message: str | None = None,
) -> None:
    """배치 실행 1회의 결과를 `IngestRuns`에 남긴다(신선도 검사가 여기를 읽는다)."""
    conn.execute(
        'INSERT INTO "IngestRuns"("RunId", "TargetTable", "Source", "RangeStartDate", '
        '"RangeEndDate", "Status", "TargetCount", "SuccessCount", "RowsWritten", '
        '"ErrorMessage", "StartedDateTime", "FinishedDateTime") '
        "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        'ON CONFLICT ("RunId") DO UPDATE SET "Status"=EXCLUDED."Status", '
        '"SuccessCount"=EXCLUDED."SuccessCount", "RowsWritten"=EXCLUDED."RowsWritten", '
        '"ErrorMessage"=EXCLUDED."ErrorMessage", '
        '"FinishedDateTime"=EXCLUDED."FinishedDateTime"',
        (run_id, target_table, source, range_start, range_end, status, target_count,
         success_count, rows_written, error_message, started_at, now_utc()),
    )
    conn.commit()


def last_ingest_run(
    conn: psycopg.Connection, target_table: str, trade_date: date
) -> dict[str, Any] | None:
    """그날 그 표의 마지막 배치 기록을 반환한다."""
    return conn.execute(
        'SELECT * FROM "IngestRuns" WHERE "TargetTable"=%s AND "RangeEndDate"=%s '
        'ORDER BY "StartedDateTime" DESC LIMIT 1',
        (target_table, trade_date),
    ).fetchone()


def load_symbol_ids(conn: psycopg.Connection) -> list[str]:
    """상장 중인 종목코드를 반환한다."""
    rows = conn.execute(
        'SELECT "SymbolId" FROM "Symbols" WHERE "DelistedDate" IS NULL '
        'ORDER BY "SymbolId"'
    ).fetchall()
    return [r["SymbolId"] for r in rows]


def load_daily_score_candidates(conn: psycopg.Connection, trade_date: date) -> list[str]:
    """그날 배치가 제외 필터를 통과시킨 종목코드를 rank 순으로 반환한다."""
    rows = conn.execute(
        'SELECT "SymbolId" FROM "DailyScores" WHERE "TradeDate"=%s AND "PassedFilter" '
        'ORDER BY "Rank" ASC NULLS LAST',
        (trade_date,),
    ).fetchall()
    return [r["SymbolId"] for r in rows]


def load_price_history(
    conn: psycopg.Connection, *, start: date, end: date, symbol_ids: list[str] | None = None
) -> dict[str, Any]:
    """DB의 일봉+수급을 종목별 시계열 dict로 반환한다(fetch_prices와 동일 형식)."""
    import pandas as pd

    sql = (
        'SELECT b."SymbolId", b."TradeDate", b."Open", b."High", b."Low", b."Close", '
        'b."Volume", f."ForeignNet", f."InstitutionNet" '
        'FROM "DailyBars" b '
        'LEFT JOIN "DailyFlows" f '
        '  ON f."SymbolId" = b."SymbolId" AND f."TradeDate" = b."TradeDate" '
        'WHERE b."TradeDate" BETWEEN %s AND %s'
    )
    params: list[Any] = [start, end]
    if symbol_ids:
        sql += ' AND b."SymbolId" = ANY(%s)'
        params.append(symbol_ids)
    rows = conn.execute(sql + ' ORDER BY b."SymbolId", b."TradeDate"', params).fetchall()
    if not rows:
        return {}
    df = pd.DataFrame(rows).rename(columns={
        "TradeDate": "date", "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
        "ForeignNet": "foreign_net", "InstitutionNet": "inst_net",
    })
    out: dict[str, Any] = {}
    for code, g in df.groupby("SymbolId"):
        out[code] = g.drop(columns=["SymbolId"]).set_index("date").sort_index()
    return out
