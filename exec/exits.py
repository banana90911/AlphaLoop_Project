"""청산 규칙 — 보유별 우선순위 결정 (exec/exits, 05-risk 5-2 §129).

순수 결정 함수(백테스트·실거래 공통). 실제 KIS 스톱 정정·집행은 별도(상주 스톱·정정 API).
매 사이클 보유 하나하나에 **우선순위 순으로 한 번에 하나만** 적용:

  ① 논지무효(invalidation_price 돌파 또는 thesis 무효) → 전량 청산
  ② 손절 도달 → 전량 청산
  ③ +tp1_R(기본 1.5R) 첫 도달 → tp1_frac 부분청산 + 잔여 손절을 진입가(본전)로 상향
  ④ ATR 트레일링: new_stop = max(old_stop, price − trail_k·ATR20)
  ⑤ 보유일 > max_hold_days 이고 진행 < +min_progress_R 이면 청산(추세 진행 중이면 면제)

R = |진입가 − 최초손절가| 로 **영구 고정**(부분익절·트레일링으로 손절이 바뀌어도 불변, §96).
롱 포지션 기준.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor

import pandas as pd

from config.settings import load_params
from core import costs
from core.timeutils import utc_iso
from memory import journal

# 청산 주문구분 — 모드별(11-2.14: 청산=13 IOC시장가). 모의 IOC 미지원이라 01 일반시장가.
EXIT_ORD_DVSN = {"real": "13", "paper": "01", "backtest": "01"}


@dataclass
class Position:
    """청산 판정에 필요한 보유 상태."""
    entry_price: float
    initial_stop: float          # 진입 시 최초 손절가 (R 산정의 기준, 불변)
    current_stop: float          # 현재 손절가 (트레일링·본전 상향으로 변동)
    days_held: int
    tp1_done: bool = False
    invalidation_price: float | None = None
    thesis_valid: bool = True


@dataclass
class ExitAction:
    """청산 결정 결과. action ∈ hold·exit_full·exit_partial·raise_stop."""
    action: str
    reason: str = ""
    fraction: float = 0.0        # exit_partial일 때 청산 비율
    new_stop: float | None = None  # exit_partial(본전)·raise_stop(트레일)일 때 새 손절


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

    # ③ +tp1_R 첫 도달 → 부분익절 + 본전 상향
    if not pos.tp1_done and risk > 0 and price >= pos.entry_price + e["tp1_R"] * risk:
        return ExitAction("exit_partial", "tp1", fraction=e["tp1_frac"],
                          new_stop=pos.entry_price)

    # ④ ATR 트레일링(손절 상향만)
    new_stop = max(pos.current_stop, price - e["trail_k"] * atr)
    if new_stop > pos.current_stop:
        return ExitAction("raise_stop", "trail", new_stop=new_stop)

    # ⑤ 시간 청산(제자리 자금 회수, 추세 진행 중이면 면제)
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
    source: str = "paper",
    llm_sells=(),
    params: dict | None = None,
    tax_params: dict | None = None,
) -> list[str]:
    """open 보유별로 청산 액션을 집행한다(7단계, 진입과 대칭). 반환: 청산 trade_id 목록.

    LLM이 sell 결정한 종목(llm_sells)은 thesis_valid=False로 ①논지무효 경로에 태운다.
    raise_stop은 내부 손절만 상향(KIS 스톱 정정은 후속), exit_full/partial은 broker로
    송출하고 체결분의 실현손익을 `outcomes`에 적재(백테스트 `_close`와 동일 costs 산식).

    한계: 라이브 잔고 동기화(선행 게이트 A.1 1번)는 미배선이라 내부 `positions`를 진실로
    본다 — 자동 체결된 KIS 스톱과의 이중주문 방지(§129)는 잔고 동기화 연결 후 완성.
    """
    from backtest.engine import build_features   # 지연 import(engine↔exits 순환 회피)
    from data.panel import latest_row            # 〃 (panel→engine→exits 체인 회피)

    p = params or load_params("risk_params")
    sells = set(llm_sells)
    rows = conn.execute("SELECT * FROM positions WHERE status='open'").fetchall()
    trade_ids: list[str] = []
    for r in rows:
        if r["qty"] <= 0:
            continue
        df = market_data.get(r["symbol"])
        if df is None or df.empty:
            continue
        frow = latest_row(build_features(df), asof)
        if frow is None:
            continue
        price, atr = frow["close"], frow["atr"]
        if pd.isna(price) or pd.isna(atr):
            continue
        pos = Position(
            entry_price=r["avg_price"],
            initial_stop=r["initial_stop_price"]
            if r["initial_stop_price"] is not None else r["current_stop_price"],
            current_stop=r["current_stop_price"],
            days_held=_days_held(r["entry_date"], asof),
            tp1_done=bool(r["tp1_done"]),
            thesis_valid=r["symbol"] not in sells,
        )
        act = decide_exit(pos, float(price), float(atr), params=p)
        if act.action == "hold":
            continue
        if act.action == "raise_stop":
            journal.update_stop(conn, r["position_id"], act.new_stop)
            continue
        sell_qty = (
            r["qty"] if act.action == "exit_full"
            else min(r["qty"], max(1, floor(r["qty"] * act.fraction)))
        )
        trade_ids.append(
            _settle_exit(conn, broker, r, sell_qty, float(price), act,
                         cycle_id, asof, order_mode, source, tax_params)
        )
    return trade_ids


def _settle_exit(conn, broker, r, sell_qty, price, act, cycle_id, asof,
                 order_mode, source, tax_params) -> str:
    """청산 1건 송출 → trades 적재 + (체결 시) outcomes 적재·positions 갱신."""
    code = r["symbol"]
    coid = f"{cycle_id}-{code}-exit-0"
    fill = broker.place_exit(
        code=code, qty=sell_qty, ord_dvsn=EXIT_ORD_DVSN[order_mode], client_order_id=coid,
    )
    filled = fill.filled_qty
    exit_price = fill.fill_price if fill.fill_price is not None else price
    journal.record_trade(
        conn, trade_id=coid, cycle_id=cycle_id, decision_id=r["entry_decision_id"],
        client_order_id=coid, symbol=code, side="sell", ord_dvsn=EXIT_ORD_DVSN[order_mode],
        order_qty=sell_qty, filled_qty=filled, order_price=0.0,
        fill_price=fill.fill_price, status=fill.status, source=source,
        filled_at=utc_iso() if filled > 0 else None,
    )
    if filled <= 0:                                   # 미체결 → 포지션 유지(에스컬레이션은 후속)
        return coid
    entry = r["avg_price"]
    mkt = r["market"] or "KOSPI"                       # 종목→시장 매핑 부재 시 기본(TODO)
    end = asof or date.today()
    buy_cost = costs.trade_cost(
        entry, filled, "buy", mkt, _as_date(r["entry_date"], end), params=tax_params
    )["total"]
    sell_cost = costs.trade_cost(exit_price, filled, "sell", mkt, end, params=tax_params)["total"]
    gross = (exit_price - entry) * filled
    net = gross - buy_cost - sell_cost
    journal.record_outcome(
        conn, outcome_id=f"{coid}-out", position_id=r["position_id"],
        entry_decision_id=r["entry_decision_id"], symbol=code, entry_price=entry,
        exit_price=exit_price, qty=filled, holding_days=_days_held(r["entry_date"], asof),
        gross_pnl=gross, net_pnl=net,
        return_pct=net / (entry * filled) if entry * filled else 0.0,
        exit_reason=act.reason, source=source,
    )
    if act.action == "exit_full" or filled >= r["qty"]:
        journal.close_position(conn, r["position_id"])
    else:
        journal.reduce_position(
            conn, r["position_id"], sell_qty=filled,
            new_stop=act.new_stop, mark_tp1=(act.reason == "tp1"),
        )
    return coid


def _days_held(entry_date: str | None, asof: date | None) -> int:
    if not entry_date:
        return 0
    try:
        ed = date.fromisoformat(entry_date[:10])
    except ValueError:
        return 0
    return max(0, ((asof or date.today()) - ed).days)


def _as_date(entry_date: str | None, fallback: date) -> date:
    if entry_date:
        try:
            return date.fromisoformat(entry_date[:10])
        except ValueError:
            pass
    return fallback
