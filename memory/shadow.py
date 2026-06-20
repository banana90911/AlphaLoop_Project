"""반사실(거부·무거래) 가상 손익 (memory/shadow, 07 7.18).

"안 산 종목이 그 후 어떻게 됐나"를 *동일 청산룰(exec/exits)·동일 비용모델*로 재생해
가상 손익을 남긴다. 결정의 기회비용을 사후 측정하는 학습 신호 — 단 **추정치**라 라벨을
달고, 실거래 보정(taken-trade)에 *혼입 금지*하며 가중을 ≤0.3으로 낮춰 쓴다(7.18).

청산은 backtest 엔진과 같은 우선순위(손절·트레일링·시간·논지무효)를 따른다. 부분익절은
단일 가상 포지션이라 손절 상향만 반영하고 전량 청산 시점의 가격으로 손익을 잡는다(근사).
"""
from __future__ import annotations

import sqlite3
from datetime import date

from core import costs
from exec.exits import Position, decide_exit


def simulate_shadow(
    entry: float, initial_stop: float,
    future_bars: list[tuple[date, float, float]], *,
    params: dict, entry_date: date, market: str = "KOSPI",
    tax_params: dict | None = None,
) -> dict:
    """가상 포지션을 미래 바((날짜, 종가, ATR) 순서)로 청산 시뮬 → 가상 결과.

    반환: virtual_exit_price·return_pct(왕복 비용 차감)·exit_reason·holding_days.
    미청산 종료면 마지막 종가로 평가(exit_reason='open_end').
    """
    pos = Position(entry, initial_stop, initial_stop, 0)
    exit_price, reason, exit_date, held = entry, "open_end", entry_date, 0
    for d, close, atr in future_bars:
        act = decide_exit(pos, close, atr, params=params)
        if act.action == "exit_full":
            exit_price, reason, exit_date, held = close, act.reason, d, pos.days_held
            break
        if act.action == "exit_partial":
            pos.tp1_done = True
            if act.new_stop is not None:
                pos.current_stop = act.new_stop
        elif act.action == "raise_stop" and act.new_stop is not None:
            pos.current_stop = act.new_stop
        pos.days_held += 1
        exit_price, exit_date, held = close, d, pos.days_held
    gross = exit_price / entry - 1.0 if entry else 0.0
    cost = costs.round_trip_cost(entry, exit_price, 1, market, entry_date, exit_date,
                                 params=tax_params) if entry else 0.0
    return {
        "virtual_exit_price": exit_price,
        "return_pct": gross - cost / entry if entry else 0.0,
        "exit_reason": reason,
        "holding_days": held,
    }


def record_shadow(
    conn: sqlite3.Connection, shadow_id: str, *, decision_id: str | None, symbol: str,
    reject_reason: str, entry: float, stop: float, target: float | None,
    sim: dict, regime_tag: str | None, created_at: str, source: str,
) -> None:
    """가상 결과를 shadow_outcomes에 적재(추정치 라벨 — source로 구분, size 미적용)."""
    conn.execute(
        "INSERT INTO shadow_outcomes(shadow_id, decision_id, symbol, reject_reason, entry, "
        "stop, target, virtual_exit_price, virtual_pnl, size_sim_applied, regime_tag, "
        "created_at, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (shadow_id, decision_id, symbol, reject_reason, entry, stop, target,
         sim["virtual_exit_price"], sim["return_pct"], 0, regime_tag, created_at, source),
    )
