"""뉴스·촉매 분석가 (agents/catalyst, 12 Phase 4·10-3.1).

코드가 못 하는 일 — *뉴스 글의 매매 의미 판단* — 에만 LLM을 쓴다. 워치리스트 중 헤드라인이
있는 종목을 **묶음 1회** 호출(비용 절감)로 평가해 종목별 촉매 견해(view·confidence·
신호·리스크)를 낸다. 결과의 score(0~1)는 코드 결정 규칙(B)·결정 에이전트(C)의 입력.

분석가는 *부분 실패 허용*(11-3.5) — 호출/파싱이 실패하면 빈 견해로 진행(촉매 없이 결정).
모델은 config/models.toml의 news 역할(저비용 Haiku). LLM은 이 칸에서만 호출.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agents.llm_client import LLMError, call_json
from core.schemas import CatalystBatch, CatalystView

_SYSTEM = (
    "너는 한국 주식 뉴스·촉매 분석가다. 각 종목의 헤드라인을 보고 매매 촉매를 평가해 "
    "JSON으로만 답하라. 형식: "
    '{"views":[{"code":"종목코드","view":"bullish|neutral|bearish",'
    '"confidence":0~1,"key_signals":["..."],"key_risks":["..."]}]}. '
    "헤드라인 근거가 약하거나 모호하면 neutral과 낮은 confidence를 매겨라(추측·과장 금지). "
    "key_signals·key_risks는 한국어 짧은 문구 배열. JSON 외 다른 텍스트 금지."
)


@dataclass
class NewsBundle:
    """종목별 뉴스 묶음(헤드라인). 펀더멘털 요약은 선택."""
    code: str
    name: str
    headlines: list[str] = field(default_factory=list)


def _build_user(bundles: list[NewsBundle]) -> str:
    return "\n".join(
        f"[{b.code} {b.name}] " + " / ".join(b.headlines) for b in bundles
    )


def analyze(bundles: list[NewsBundle]) -> list[CatalystView]:
    """헤드라인 있는 종목들을 1회 호출로 분석 → 종목별 촉매 견해. 실패 시 빈 리스트(부분 실패)."""
    bundles = [b for b in bundles if b.headlines]
    if not bundles:
        return []
    try:
        result = call_json("news", _SYSTEM, _build_user(bundles), CatalystBatch)
    except LLMError:
        return []                       # 분석가 부분 실패 허용 → 촉매 없이 진행
    return result.data.views
