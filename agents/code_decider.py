"""B안 — 코드 규칙 결정 (agents/code_decider, 10-3.1 B·P1-3).

결정 에이전트(C, LLM) 없이 *종합점수 임계*로만 결정하는 가장 싸고 단순한 완결 버전.
3자 비교(A/B/C)의 대조군 — C와의 *유일한* 차이가 "결정 주체(코드 임계 vs LLM 판단)"가
되도록, 사이징·청산·리스크엔진·워치리스트·비용모델은 C와 100% 동일하게 둔다.

종합점수 = 스크리닝(코드) ⊕ 촉매(뉴스 0~1) ⊕ 보정신뢰(calibration) 가중합(가중치 config).
score ≥ 진입임계 τ → buy(신규), 보유는 촉매·점수가 무효임계 아래로 떨어지면 청산 제안
(실제 청산 우선순위는 C와 동일하게 exec/exits.py가 집행). LLM 미관여(비용 0).
"""
from __future__ import annotations

from dataclasses import dataclass

from core.schemas import DeciderOutput, OrderAction, ProposedOrder


@dataclass
class Candidate:
    """결정 입력. catalyst_score=None이면 뉴스 없음(중립 취급, 가중에서 제외)."""
    code: str
    screen_score: float                 # 스크리너 종합점수 0~1
    catalyst_score: float | None = None  # 촉매 점수 0~1 (CatalystView.score)


def composite_score(screen: float, catalyst: float | None, weights: dict) -> float:
    """가중 합산 종합점수 0~1. 결측 항목은 가중에서 빼고 재정규화(한 결측이 종목을 안 떨굼)."""
    parts: list[tuple[float, float]] = [(weights["w_screen"], screen)]
    if catalyst is not None:
        parts.append((weights["w_catalyst"], catalyst))
    # 보정신뢰(w_calibration)는 calibration 구현 후 추가 — 현재 0이라 생략
    wsum = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / wsum if wsum else 0.0


def decide(
    candidates: list[Candidate], held_codes: set[str], params: dict
) -> DeciderOutput:
    """종합점수 임계 결정. 신규=buy(score≥τ), 보유=무효면 sell·아니면 hold."""
    d = params["decision"]
    orders: list[ProposedOrder] = []
    for c in candidates:
        sc = composite_score(c.screen_score, c.catalyst_score, d)
        thesis = f"B안 종합점수 {sc:.2f}"
        if c.code in held_codes:
            action = OrderAction.SELL if sc < d["exit_threshold"] else OrderAction.HOLD
            orders.append(ProposedOrder(code=c.code, action=action,
                                        risk_budget=sc, thesis=thesis))
        elif sc >= d["entry_threshold"]:
            orders.append(ProposedOrder(code=c.code, action=OrderAction.BUY,
                                        risk_budget=sc, thesis=thesis))
    return DeciderOutput(orders=orders, notes="code_decider(B안)")
