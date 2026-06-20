"""학습 테이블 시드 (backtest/seed, 12 Phase 6·05-risk 5-2·7.19).

백테스트 재생 결과(거래)를 journal의 `outcomes`(source='backtest')에 선채워 학습 루프가
*1일차부터* 작동하게 한다 — 라이브만으론 칸이 차는 데 수개월 걸린다. 켈리 입력 p·b는
*가격 사실*이라 모델 버전과 무관(7.19)하므로, 시드로 채우면 sizing 켈리 천장이 출발부터
활성될 수 있다.

raw 시세·수급(백테스트 입력)은 폐기 대상 캐시지만, 이 *결과*는 journal에 source 라벨로
남아 실전 전환 후에도 학습 시드로 유지된다(data/cache 주석의 ①/② 구분).
"""
from __future__ import annotations

import sqlite3

from backtest.engine import ClosedTrade


def kelly_pb(trades: list[ClosedTrade]) -> tuple[float, float] | None:
    """거래 결과 → (승률 p, 손익비 b). 이익·손실 표본이 둘 다 있어야 산출(없으면 None).

    p = 이긴 거래 비율, b = 평균 이익금액 ÷ 평균 손실금액. sizing 켈리 천장 입력.
    """
    if not trades:
        return None
    wins = [t.net_pnl for t in trades if t.net_pnl > 0]
    losses = [-t.net_pnl for t in trades if t.net_pnl < 0]
    if not wins or not losses:
        return None
    p = len(wins) / len(trades)
    b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    return (p, b)


def seed_outcomes(
    conn: sqlite3.Connection, trades: list[ClosedTrade], *, source: str = "backtest"
) -> int:
    """ClosedTrade들을 outcomes에 적재(학습 시드). 적재 건수 반환. entry_decision_id는 NULL."""
    rows = 0
    for i, t in enumerate(trades):
        ret = (t.exit_price / t.entry_price - 1.0) if t.entry_price else 0.0
        held = (t.exit_date - t.entry_date).days
        conn.execute(
            "INSERT INTO outcomes(outcome_id, symbol, entry_price, exit_price, qty, "
            "holding_days, net_pnl, return_pct, exit_reason, closed_at, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"seed_{source}_{i}_{t.code}_{t.exit_date.isoformat()}", t.code,
             t.entry_price, t.exit_price, t.qty, held, t.net_pnl, ret, t.reason,
             t.exit_date.isoformat(), source),
        )
        rows += 1
    return rows
