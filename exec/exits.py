"""청산 규칙 — 보유별 우선순위 결정 (exec/exits, 06-sizing §129).

순수 결정 함수(백테스트·실거래 공통). 실제 KIS 스톱 정정·집행은 별도(상주 스톱·정정 API).
매 사이클 보유 하나하나에 **우선순위 순으로 한 번에 하나만** 적용:

  ① 논지무효(invalidation_price 돌파 또는 thesis 무효) → 전량 청산
  ② 손절 도달 → 전량 청산
  ③ +breakeven_R(기본 1.5R) 첫 도달 → 손절 본전 상향 (partial_frac>0이면 그만큼 부분 청산.
    기본값은 0 — 부분 익절은 "이익은 길게"와 어긋나 두지 않는다, 06-sizing 6.2)
  ④ ATR 트레일링: new_stop = max(old_stop, price − trail_k·ATR20)
  ⑤ 보유일 > max_hold_days · 진행 < +min_progress_R · ④로 손절을 더 못 올림 → 전량 청산

⑤가 ④ 뒤인 것이 규칙의 일부다 — 손절을 계속 올릴 수 있으면(=오르는 중이면) 시간청산이
걸리지 않는다. 순서를 바꿔 시간청산을 앞에 두면 느리게 오르는 승자를 잘라낸다.
R = |진입가 − 최초손절가| 로 **영구 고정**(부분 청산·트레일링으로 손절이 바뀌어도 불변).
롱 포지션 기준.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from config.settings import load_params
from core import costs
from core.timeutils import kst_today, now_utc
from memory import journal

# 청산 주문구분 — 모드별(10-ops 10.14: 청산=13 IOC시장가). 모의 IOC 미지원이라 01 일반시장가.
EXIT_ORD_DVSN = {"real": "13", "paper": "01", "backtest": "01"}

# 청산 사유 — 내부 표기(snake) → Outcomes.ExitReason 값(07-model CHECK 제약).
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
    """손절 구멍 감지 입력 — 잔고 동기화 후의 보유(종목·현재 손절가·수량)."""
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
    """현재가가 손절 트리거를 이탈했는데도 아직 청산 안 된 보유를 감지한다 — 손절 구멍 트리거(03-arch 폴링 트리거).

    스톱지정가(22)가 개장 갭·급락으로 미체결로 남으면 KIS 잔고에 포지션이 그대로 남는다.
    그 상태(현재가 ≤ 손절가인데 qty>0)를 이벤트 사이클 트리거로 삼아, 사이클의 execute_exits
    ②(손절 도달 → 시장가)가 강제 정리하게 한다. 감시 단계 판정이라 주문을 내지 않고
    *깨울 대상만* 반환한다 — decide_exit ②와 임계는 같지만(price ≤ stop) 역할이 다르다.

    positions: 잔고 동기화(선행 게이트 A.1 1번) 후 값을 기대한다 — 그래야 밤사이 자동 체결된
      스톱이 잔고에 반영돼 *이미 팔린 종목을 손절 구멍으로 오인*하지 않는다.
    prices: symbol → 현재가. 결측·비정상(None·≤0)은 건너뛴다(감시는 다음 폴링에서 재시도).
    """
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
    """현재가·ATR로 청산 액션 하나를 결정. 우선순위 순 첫 매칭."""
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
        # max: 트레일링이 이미 본전 위로 올려둔 경우 손절을 내리지 않는다
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
    order_mode: str = "paper",
    mode: str = "paper",
    forced_sells=(),
    params: dict | None = None,
    tax_params: dict | None = None,
) -> list[str]:
    """open 보유별로 청산 액션을 집행한다(5~6단계, 진입과 대칭). 반환: 청산 ClientOrderId 목록.

    결정 규칙이 sell을 낸 종목(forced_sells)은 thesis_valid=False로 ①논지무효 경로에 태운다.
    raise_stop은 내부 손절만 상향(KIS 스톱 정정은 후속), exit_full은 broker로 송출하고
    체결분의 실현손익을 `Outcomes`에 적재(백테스트 `_close`와 동일 costs 산식).

    한계: 라이브 잔고 동기화(선행 게이트 A.1 1번)는 미배선이라 내부 `Positions`를 진실로
    본다 — 자동 체결된 KIS 스톱과의 이중주문 방지(§129)는 잔고 동기화 연결 후 완성.
    """
    from data.features import build_features  # 지연 import(features↔exits 순환 회피)
    from data.panel import latest_row

    p = params or load_params("risk_params")
    sells = set(forced_sells)
    rows = conn.execute(
        'SELECT * FROM "Positions" WHERE "Status" = \'open\''
    ).fetchall()
    order_ids: list[str] = []
    for r in rows:
        if r["Quantity"] <= 0:
            continue
        df = market_data.get(r["SymbolId"])
        if df is None or df.empty:
            continue
        frow = latest_row(build_features(df), asof)
        if frow is None:
            continue
        price, atr = frow["close"], frow["atr"]
        if pd.isna(price) or pd.isna(atr):
            continue
        pos = Position(
            entry_price=r["AveragePrice"],
            initial_stop=r["InitialStopPrice"]
            if r["InitialStopPrice"] is not None else r["CurrentStopPrice"],
            current_stop=r["CurrentStopPrice"],
            days_held=_days_held(r["EntryDate"], asof),
            breakeven_done=bool(r["IsBreakevenDone"]),
            thesis_valid=r["SymbolId"] not in sells,
        )
        act = decide_exit(pos, float(price), float(atr), params=p)
        if act.action == "hold":
            continue
        if act.action == "raise_stop":
            # 본전 상향이면 완료 표시까지 남긴다 — 안 남기면 다음 사이클에 ③이 또 걸린다.
            journal.update_stop(
                conn, r["PositionId"], act.new_stop,
                breakeven_done=True if act.reason == "breakeven" else None,
            )
            continue
        sell_qty = (
            r["Quantity"] if act.action == "exit_full"
            else min(r["Quantity"], max(1, int(r["Quantity"] * act.fraction)))
        )
        order_ids.append(
            _settle_exit(conn, broker, r, sell_qty, float(price), act,
                         cycle_id, asof, order_mode, mode, tax_params)
        )
    return order_ids


def _settle_exit(conn, broker, r, sell_qty, price, act, cycle_id, asof,
                 order_mode, mode, tax_params) -> str:
    """청산 1건 송출 → Orders 적재 + (체결 시) Outcomes 적재·Positions 갱신."""
    code = r["SymbolId"]
    coid = f"{cycle_id}-{code}-exit-0"
    fill = broker.place_exit(
        code=code, qty=sell_qty, ord_dvsn=EXIT_ORD_DVSN[order_mode], client_order_id=coid,
    )
    filled = fill.filled_qty
    exit_price = fill.fill_price if fill.fill_price is not None else price
    journal.record_order(
        conn, client_order_id=coid, cycle_id=cycle_id, decision_id=r["EntryDecisionId"],
        symbol_id=code, side="sell", purpose="exit", order_type=EXIT_ORD_DVSN[order_mode],
        order_quantity=sell_qty, filled_quantity=filled, order_price=0.0,
        average_fill_price=fill.fill_price, kis_order_no=fill.broker_order_id,
        status=fill.status, mode=mode,
        filled_at=now_utc() if filled > 0 else None,
    )
    if filled <= 0:                                   # 미체결 → 포지션 유지(에스컬레이션은 후속)
        return coid
    entry = float(r["AveragePrice"])
    mkt = r["Market"] or "KOSPI"                       # 종목→시장 매핑 부재 시 기본(TODO)
    end = asof or kst_today()
    entry_date = _as_date(r["EntryDate"], end)
    buy_cost = costs.trade_cost(entry, filled, "buy", mkt, entry_date, params=tax_params)
    sell_cost = costs.trade_cost(exit_price, filled, "sell", mkt, end, params=tax_params)
    gross = (exit_price - entry) * filled
    net = gross - buy_cost["total"] - sell_cost["total"]
    risk = r["InitialStopPrice"]
    r_per_share = entry - float(risk) if risk is not None else None
    journal.record_outcome(
        conn, outcome_id=f"{coid}-out", position_id=r["PositionId"],
        entry_decision_id=r["EntryDecisionId"], symbol_id=code, entry_price=entry,
        exit_price=exit_price, quantity=filled,
        entry_date=entry_date, exit_date=end,
        holding_days=_days_held(r["EntryDate"], asof),
        gross_profit_loss=gross, net_profit_loss=net,
        fee=buy_cost["commission"] + sell_cost["commission"], tax=sell_cost["tax"],
        return_percent=net / (entry * filled) if entry * filled else 0.0,
        r_multiple=net / (r_per_share * filled) if r_per_share else None,
        exit_kind="full" if act.action == "exit_full" else "partial",
        exit_reason=EXIT_REASONS[act.reason], mode=mode,
    )
    if act.action == "exit_full" or filled >= r["Quantity"]:
        journal.close_position(conn, r["PositionId"])
    else:
        journal.reduce_position(
            conn, r["PositionId"], sell_quantity=filled, new_stop=act.new_stop,
        )
    return coid


def _days_held(entry_date: date | None, asof: date | None) -> int:
    ed = _as_date(entry_date, None)
    if ed is None:
        return 0
    return max(0, ((asof or kst_today()) - ed).days)


def _as_date(entry_date, fallback):
    """EntryDate는 date 컬럼이라 보통 date로 온다 — 문자열이 섞여 와도 견디게 둔다."""
    if isinstance(entry_date, date):
        return entry_date
    if entry_date:
        try:
            return date.fromisoformat(str(entry_date)[:10])
        except ValueError:
            pass
    return fallback
