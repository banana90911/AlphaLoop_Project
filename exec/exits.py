"""
description:        청산 규칙 — 보유별 우선순위 결정 (논지무효→손절→본전→트레일→시간청산)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from config.settings import load_params
from core import costs
from core.timeutils import kst_today, now_utc
from core.trading_days import trading_days_between
from memory import journal

# 청산 주문구분 — 모드별(모의 IOC 미지원이라 01 일반시장가).
EXIT_ORD_DVSN = {"real": "13", "paper": "01", "backtest": "01"}

# 청산 사유 — 내부 표기(snake) → Outcomes.ExitReason 값.
EXIT_REASONS = {
    "thesis_invalid": "thesisInvalid",
    "stop_hit": "stopHit",
    "time_exit": "timeExit",
    "breakeven": "breakeven",
    "trail": "trail",
}


@dataclass
class Position:
    """청산 판정에 필요한 보유 상태."""
    entry_price: float
    initial_stop: float          # 진입 시 최초 손절가 (R 산정의 기준, 불변)
    current_stop: float          # 현재 손절가 (트레일링·본전 상향으로 변동)
    days_held: int
    breakeven_done: bool = False   # 본전 상향 완료 (중복 실행 방지)
    invalidation_price: float | None = None
    thesis_valid: bool = True


@dataclass
class StopPosition:
    """손절 구멍 감지 입력 — 보유(종목·현재 손절가·수량)."""
    symbol: str
    current_stop: float
    qty: int


@dataclass
class StopGapHit:
    """손절 구멍(스톱 미체결) 감지 결과 — 이벤트 사이클 트리거 대상."""
    symbol: str
    price: float
    stop: float


def detect_stop_gaps(
    positions: list[StopPosition], prices: dict[str, float]
) -> list[StopGapHit]:
    """현재가가 손절 트리거를 이탈했는데도 청산 안 된 보유(손절 구멍)를 감지한다."""
    hits: list[StopGapHit] = []
    for p in positions:
        if p.qty <= 0:
            continue
        price = prices.get(p.symbol)
        if price is None or price <= 0:
            continue
        if price <= p.current_stop:
            hits.append(StopGapHit(p.symbol, float(price), float(p.current_stop)))
    return hits


@dataclass
class ExitAction:
    """청산 결정 결과. action ∈ hold·exit_full·exit_partial·raise_stop."""
    action: str
    reason: str = ""
    new_stop: float | None = None   # raise_stop(본전·트레일)·exit_partial일 때 새 손절
    fraction: float = 0.0           # exit_partial일 때 청산 비율


def decide_exit(pos: Position, price: float, atr: float, *, params: dict | None = None
                ) -> ExitAction:
    """현재가·ATR로 청산 액션 하나를 결정한다. 우선순위 순 첫 매칭."""
    e = (params or load_params("risk_params"))["exits"]
    risk = pos.entry_price - pos.initial_stop   # R (롱: 양수 가정)

    # ① 논지무효
    if not pos.thesis_valid or (
        pos.invalidation_price is not None and price <= pos.invalidation_price
    ):
        return ExitAction("exit_full", "thesis_invalid")

    # ② 손절 도달
    if price <= pos.current_stop:
        return ExitAction("exit_full", "stop_hit")

    # ③ +breakeven_R 첫 도달 → 손절 본전 상향(partial_frac>0이면 그만큼 부분 청산)
    if (
        not pos.breakeven_done
        and risk > 0
        and price >= pos.entry_price + e["breakeven_R"] * risk
    ):
        new_stop = max(pos.current_stop, pos.entry_price)
        frac = float(e.get("partial_frac", 0.0))
        if frac > 0:
            return ExitAction("exit_partial", "breakeven", new_stop=new_stop, fraction=frac)
        return ExitAction("raise_stop", "breakeven", new_stop=new_stop)

    # ④ ATR 트레일링(손절 상향만) — 갱신되면 ⑤를 검사하지 않는다
    new_stop = max(pos.current_stop, price - e["trail_k"] * atr)
    if new_stop > pos.current_stop:
        return ExitAction("raise_stop", "trail", new_stop=new_stop)

    # ⑤ 시간 청산 — 오래 들고 있는데 진행이 없고, 손절도 더 못 올리는 제자리
    if (
        pos.days_held > e["max_hold_days"]
        and risk > 0
        and price < pos.entry_price + e["min_progress_R"] * risk
    ):
        return ExitAction("exit_full", "time_exit")

    return ExitAction("hold")


# ── 청산 집행 (오케스트레이션 — 보유별 decide_exit → 송출 → trades·outcomes·positions) ──
def execute_exits(
    conn,
    market_data,
    *,
    broker,
    cycle_id: str,
    asof: date | None = None,
    trade_date: date | None = None,
    last_prices: dict[str, float] | None = None,
    order_mode: str = "paper",
    mode: str = "paper",
    forced_sells=(),
    params: dict | None = None,
    tax_params: dict | None = None,
) -> list[str]:
    """open 보유별로 청산 액션을 집행한다. 반환: 청산 ClientOrderId 목록.

    `asof`는 지표 기준일(전일 확정 봉), `trade_date`는 사이클이 도는 날이다.
    ATR·보유일수는 전자, 현재가·비용 날짜는 후자를 쓴다(04-data 4.2).
    """
    from data.features import build_features  # 지연 import(features↔exits 순환 회피)
    from data.panel import latest_row

    p = params or load_params("risk_params")
    sells = set(forced_sells)
    prices_now = last_prices or {}
    day = trade_date or kst_today()
    rows = conn.execute(
        'SELECT * FROM positions WHERE status = \'open\''
    ).fetchall()
    order_ids: list[str] = []
    for r in rows:
        if r["quantity"] <= 0:
            continue
        code = r["symbol_id"]
        df = market_data.get(code)
        if df is None or df.empty:
            continue
        frow = latest_row(build_features(df), asof)
        if frow is None:
            continue
        atr = frow["atr"]                       # 전일 확정 봉의 ATR
        # 청산 판정 가격은 사이클 시점 현재가. 시세를 못 받았으면 전일 종가로 대신한다
        price = prices_now.get(code, frow["close"])
        if pd.isna(price) or pd.isna(atr):
            continue
        pos = Position(
            entry_price=r["average_price"],
            initial_stop=r["initial_stop_price"]
            if r["initial_stop_price"] is not None else r["current_stop_price"],
            current_stop=r["current_stop_price"],
            days_held=_days_held(r["entry_date"], day),
            breakeven_done=bool(r["is_breakeven_done"]),
            thesis_valid=code not in sells,
        )
        act = decide_exit(pos, float(price), float(atr), params=p)
        if act.action == "hold":
            continue
        if act.action == "raise_stop":
            # 본전 상향이면 완료 표시까지 남긴다 — 안 남기면 다음 사이클에 ③이 또 걸린다.
            journal.update_stop(
                conn, r["position_id"], act.new_stop,
                breakeven_done=True if act.reason == "breakeven" else None,
            )
            continue
        sell_qty = (
            r["quantity"] if act.action == "exit_full"
            else min(r["quantity"], max(1, int(r["quantity"] * act.fraction)))
        )
        order_ids.append(
            _settle_exit(conn, broker, r, sell_qty, float(price), act,
                         cycle_id, day, order_mode, mode, tax_params)
        )
    return order_ids


def _settle_exit(conn, broker, r, sell_qty, price, act, cycle_id, trade_date,
                 order_mode, mode, tax_params) -> str:
    """청산 1건 송출 → Orders 적재 + (체결 시) Outcomes 적재·Positions 갱신.

    `trade_date`는 청산이 일어난 날 — 거래세율·보유일수 산정의 기준이다.
    """
    code = r["symbol_id"]
    coid = f"{cycle_id}-{code}-exit-0"
    fill = broker.place_exit(
        code=code, qty=sell_qty, ord_dvsn=EXIT_ORD_DVSN[order_mode], client_order_id=coid,
    )
    filled = fill.filled_qty
    exit_price = fill.fill_price if fill.fill_price is not None else price
    journal.record_order(
        conn, client_order_id=coid, cycle_id=cycle_id, decision_id=r["entry_decision_id"],
        symbol_id=code, side="sell", purpose="exit", order_type=EXIT_ORD_DVSN[order_mode],
        order_quantity=sell_qty, filled_quantity=filled, order_price=0.0,
        average_fill_price=fill.fill_price, kis_order_no=fill.broker_order_id,
        status=fill.status, mode=mode,
        filled_at=now_utc() if filled > 0 else None,
    )
    if filled <= 0:                                   # 미체결 → 포지션 유지(에스컬레이션은 후속)
        return coid
    entry = float(r["average_price"])
    mkt = r["market"] or "KOSPI"                       # 종목→시장 매핑 부재 시 기본(TODO)
    end = trade_date or kst_today()
    entry_date = _as_date(r["entry_date"], end)
    buy_cost = costs.trade_cost(entry, filled, "buy", mkt, entry_date, params=tax_params)
    sell_cost = costs.trade_cost(exit_price, filled, "sell", mkt, end, params=tax_params)
    gross = (exit_price - entry) * filled
    net = gross - buy_cost["total"] - sell_cost["total"]
    risk = r["initial_stop_price"]
    r_per_share = entry - float(risk) if risk is not None else None
    journal.record_outcome(
        conn, outcome_id=f"{coid}-out", position_id=r["position_id"],
        entry_decision_id=r["entry_decision_id"], symbol_id=code, entry_price=entry,
        exit_price=exit_price, quantity=filled,
        entry_date=entry_date, exit_date=end,
        holding_days=_days_held(r["entry_date"], end),
        gross_profit_loss=gross, net_profit_loss=net,
        fee=buy_cost["commission"] + sell_cost["commission"], tax=sell_cost["tax"],
        return_percent=net / (entry * filled) if entry * filled else 0.0,
        r_multiple=net / (r_per_share * filled) if r_per_share else None,
        exit_kind="full" if act.action == "exit_full" else "partial",
        exit_reason=EXIT_REASONS[act.reason], mode=mode,
    )
    if act.action == "exit_full" or filled >= r["quantity"]:
        journal.close_position(conn, r["position_id"])
    else:
        journal.reduce_position(
            conn, r["position_id"], sell_quantity=filled, new_stop=act.new_stop,
        )
    return coid


def _days_held(entry_date: date | None, asof: date | None) -> int:
    """진입일로부터 asof까지의 보유일수를 **거래일로** 센다.

    청산 규칙의 단위가 거래일이기 때문이다(06-sizing 6.2 "20거래일 초과").
    달력일로 세면 주말·연휴만큼 일찍 잘려 백테스트와 결과가 갈린다.
    """
    ed = _as_date(entry_date, None)
    if ed is None:
        return 0
    return trading_days_between(ed, asof or kst_today())


def _as_date(entry_date, fallback):
    """EntryDate를 date로 정규화한다(문자열이 섞여 와도 견딤)."""
    if isinstance(entry_date, date):
        return entry_date
    if entry_date:
        try:
            return date.fromisoformat(str(entry_date)[:10])
        except ValueError:
            pass
    return fallback
