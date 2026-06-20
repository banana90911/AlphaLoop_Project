"""결정 에이전트 (C안, agents/decider, 12 Phase 4·10-3.1·P1-2).

코드가 못 하는 두 번째 일 — *최종 매매 결정* — 에 LLM을 쓴다. 코드가 준 종목 점수·촉매
견해·보유현황·리스크 제약을 종합해 제안 주문(신규+보유 동적관리)을 낸다. 수량 환산은
코드(sizing)가 하므로 LLM은 방향·논지·위험예산만 낸다(환각이 수량·트리거에 직접 닿지 않게).

반대 시나리오는 *별도 호출 없이* 지시문에 포함하고, 반영도를 dissent_addressed로 받아
0.6 미만(반대 우세)이면 신규/추가를 강제 무거래로 거른다(P1-2). 결정자는 *부분 실패
불가* — call_json 실패(LLMError)는 호출측(pipeline)이 사이클 중단으로 처리한다(11-3.5).
모델은 config/models.toml decision 역할(Sonnet). C와 B의 유일한 차이는 결정 주체.
"""
from __future__ import annotations

from agents.code_decider import Candidate
from agents.llm_client import call_json
from core.schemas import DeciderOutput, OrderAction

_SYSTEM = (
    "너는 한국 주식 스윙 매매 결정자다. 코드가 준 종목 점수·촉매 견해·보유현황·리스크 제약을 "
    "종합해 제안 주문을 JSON으로만 내라. 형식: "
    '{"orders":[{"code":"종목코드","action":"buy|add|hold|trim|sell",'
    '"risk_budget":0~1,"thesis":"진입/보유 논지","invalidation_price":숫자 또는 null,'
    '"dissent_addressed":0~1}],"notes":"요약"}. '
    "각 제안마다 *반대 시나리오*(이 판단이 틀릴 수 있는 이유)를 스스로 검토하라. "
    "dissent_addressed는 그 반대를 *얼마나 충분히 검토·반박해 진입을 확신하는지*를 뜻한다 "
    "(1=반대를 충분히 해소해 강하게 확신, 0=반대를 못 이겨 확신 없음). 강한 호재·근거가 "
    "뚜렷하면 높게, 근거가 약하거나 반대가 우세하면 낮게. dissent_addressed가 0.6 미만인 "
    "종목은 확신 부족이니 orders에서 빼라(무거래). "
    "수량은 정하지 말고(코드가 환산) 확신 강도만 risk_budget(0~1)으로. JSON 외 텍스트 금지."
)


def _build_user(
    candidates: list[Candidate], holdings: list[str], cash: float, equity: float
) -> str:
    lines = ["[후보 종목]"]
    for c in candidates:
        cat = f"촉매 {c.catalyst_score:.2f}" if c.catalyst_score is not None else "촉매 없음"
        lines.append(f"- {c.code}: 스크리너 {c.screen_score:.2f}, {cat}")
    lines.append(f"[보유] {', '.join(holdings) if holdings else '없음'}")
    lines.append(f"[제약] 가용현금 {cash:,.0f}원 / 평가자본 {equity:,.0f}원")
    return "\n".join(lines)


def _apply_dissent_gate(out: DeciderOutput, dissent_min: float) -> DeciderOutput:
    """반대 우세(dissent_addressed < 임계) 신규/추가는 강제 무거래로 제거 (P1-2)."""
    kept = [
        o for o in out.orders
        if not (o.action in (OrderAction.BUY, OrderAction.ADD)
                and o.dissent_addressed < dissent_min)
    ]
    return DeciderOutput(orders=kept, notes=out.notes)


def decide(
    candidates: list[Candidate], holdings: list[str], *,
    cash: float, equity: float, params: dict,
) -> DeciderOutput:
    """LLM 결정 → 반대의견 게이트 적용. 실패(LLMError)는 호출측이 사이클 중단."""
    user = _build_user(candidates, holdings, cash, equity)
    result = call_json("decision", _SYSTEM, user, DeciderOutput)
    return _apply_dissent_gate(result.data, params["decision"]["dissent_min"])
