"""LLM 입출력 스키마 — 부분 파싱 금지의 구현체 (core/schemas, 03-arch·11-3.3).

pydantic v2 모델로 LLM 응답을 *전체 검증*한다 — 필수 필드 누락·범위 위반이면 무효
(절반 결정을 결정으로 채택하지 않는다, 11-3.3). catalyst(뉴스 분석)·decider(결정)
출력이 코드 결정 규칙·리스크 엔진으로 안전하게 흐르도록 형(型)을 고정한다.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class MarketView(StrEnum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class CatalystView(BaseModel):
    """뉴스·촉매 분석가 출력 (agents/catalyst — 12 Phase 4·10-3.1)."""
    model_config = {"extra": "forbid"}

    code: str
    view: MarketView
    confidence: float = Field(ge=0.0, le=1.0)
    key_signals: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)

    def score(self) -> float:
        """촉매 점수 0~1 (B안 종합점수 입력). bullish=0.5+0.5·conf, bearish 대칭, neutral=0.5."""
        if self.view is MarketView.BULLISH:
            return 0.5 + 0.5 * self.confidence
        if self.view is MarketView.BEARISH:
            return 0.5 - 0.5 * self.confidence
        return 0.5


class OrderAction(StrEnum):
    BUY = "buy"      # 신규 진입
    ADD = "add"      # 추가 매수
    HOLD = "hold"    # 유지
    TRIM = "trim"    # 부분 청산
    SELL = "sell"    # 전량 청산


class ProposedOrder(BaseModel):
    """결정 에이전트의 종목별 제안. *수량* 환산은 코드(sizing)가 — LLM은 방향·논지·예산만."""
    model_config = {"extra": "forbid"}

    code: str
    action: OrderAction
    risk_budget: float = Field(default=0.0, ge=0.0, le=1.0)   # 거래당 위험 예산(0~1)
    thesis: str = ""                                          # entry_thesis 논지
    invalidation_price: float | None = None                  # 논지 무효가(exits 논지무효)
    # 반대 시나리오 반영도(P1-2: <0.6이면 pipeline이 강제 무거래)
    dissent_addressed: float = Field(default=1.0, ge=0.0, le=1.0)


class DeciderOutput(BaseModel):
    """결정 주체(LLM decider 또는 B안 코드)의 사이클 출력. 신규+보유 동적 관리 제안."""
    model_config = {"extra": "forbid"}

    orders: list[ProposedOrder] = Field(default_factory=list)
    notes: str = ""
