"""결정 흐름 조립 — 3자 비교 진입점 (pipeline/decision, 03-arch 4·6단계·10-3.1).

후보(스크리너 산출)+뉴스 → 촉매 분석 → 결정 → 제안 주문. 한 진입점에서 모드로 전환해
*결정 주체 하나만* 바꾼 A/B/C를 같은 입력으로 비교한다(10-3.1 통제 비교의 코드면):
- A(코드만): 촉매 없음 + 코드 규칙 결정 — LLM 0
- B(뉴스+코드): 촉매 분석 + 코드 규칙 결정
- C(뉴스+LLM): 촉매 분석 + 결정 에이전트(LLM)

드라이런: 제안 주문(DeciderOutput)만 반환한다 — 수량 환산·리스크 게이트·주문 송출은
후속(sizing·risk_engine·exec). 결정자(C) 실패는 호출측이 사이클 중단으로 처리(11-3.5).
"""
from __future__ import annotations

from agents import catalyst, code_decider, decider
from agents.catalyst import NewsBundle
from agents.code_decider import Candidate
from core.schemas import DeciderOutput

_MODES = ("A", "B", "C")


def run_decision(
    candidates: list[Candidate],
    news_bundles: list[NewsBundle],
    holdings: list[str],
    *,
    cash: float,
    equity: float,
    params: dict,
    mode: str = "C",
) -> DeciderOutput:
    """모드별 결정. candidates는 스크리너 점수 보유(촉매는 여기서 분석해 결합)."""
    if mode not in _MODES:
        raise ValueError(f"mode는 {_MODES} 중 하나: {mode!r}")

    catalyst_scores: dict[str, float] = {}
    if mode in ("B", "C"):                       # A는 뉴스 LLM 미사용
        for view in catalyst.analyze(news_bundles):
            catalyst_scores[view.code] = view.score()

    enriched = [
        Candidate(c.code, c.screen_score, catalyst_scores.get(c.code))
        for c in candidates
    ]

    if mode == "C":                              # 결정 에이전트(LLM)
        return decider.decide(enriched, holdings, cash=cash, equity=equity, params=params)
    return code_decider.decide(enriched, set(holdings), params)   # A·B: 코드 규칙
