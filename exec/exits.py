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

from config.settings import load_params


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
