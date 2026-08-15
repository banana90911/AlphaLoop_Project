"""결정 — 코드 규칙(통계) 단일 경로 (pipeline/decision, 03-arch 4단계).

스크리너 종합점수 임계로만 결정하는 결정론 규칙이다. 초기 구축 단계에서는 결정 주체가
코드 하나뿐이라 모드 분기가 없다 — 뉴스·촉매 판단이나 LLM 결정자는 두지 않는다.

score ≥ 진입임계 τ → buy(신규), 보유는 점수가 무효임계 아래로 떨어지면 sell 제안
(실제 청산 우선순위는 exec/exits.py가 집행). 수량 환산은 risk/sizing.py.

드라이런: 제안 주문(DeciderOutput)만 반환한다 — 수량 환산·리스크 게이트·주문 송출은
후속(sizing·risk_engine·exec).
"""
from __future__ import annotations

from dataclasses import dataclass

from core.schemas import DeciderOutput, OrderAction, ProposedOrder


@dataclass
class Candidate:
    """결정 입력 — 스크리너가 낸 종목별 종합점수(0~1)."""
    code: str
    screen_score: float


def decide(
    candidates: list[Candidate], held_codes: set[str], params: dict
) -> DeciderOutput:
    """점수 임계 결정. 신규=buy(score≥τ), 보유=무효면 sell·아니면 hold."""
    d = params["decision"]
    orders: list[ProposedOrder] = []
    for c in candidates:
        sc = c.screen_score
        thesis = f"스크리너 점수 {sc:.2f}"
        if c.code in held_codes:
            action = OrderAction.SELL if sc < d["exit_threshold"] else OrderAction.HOLD
            orders.append(ProposedOrder(code=c.code, action=action,
                                        risk_budget=sc, thesis=thesis))
        elif sc >= d["entry_threshold"]:
            orders.append(ProposedOrder(code=c.code, action=OrderAction.BUY,
                                        risk_budget=sc, thesis=thesis))
    return DeciderOutput(orders=orders, notes="rule_decider")


def run_decision(
    candidates: list[Candidate],
    holdings: list[str],
    *,
    params: dict,
) -> DeciderOutput:
    """사이클 결정 진입점(trading_cycle 4단계). 현재는 규칙 결정 한 경로."""
    return decide(candidates, set(holdings), params)
