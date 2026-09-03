"""
description:        결정 — 점수 임계 규칙 단일 경로 (신규 진입·보유 청산 제안)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

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
    """사이클 결정 진입점(pipeline.cycle 3단계)."""
    return decide(candidates, set(holdings), params)
