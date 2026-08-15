"""결정 입출력 스키마 (core/schemas, 03-arch).

pydantic v2 모델로 결정 출력을 *전체 검증*한다 — 필수 필드 누락·범위 위반이면 무효
(절반 결정을 결정으로 채택하지 않는다). 결정 규칙(pipeline/decision)의 출력이
리스크 엔진·집행으로 안전하게 흐르도록 형(型)을 고정한다.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class OrderAction(StrEnum):
    BUY = "buy"      # 신규 진입
    ADD = "add"      # 추가 매수
    HOLD = "hold"    # 유지
    TRIM = "trim"    # 부분 청산
    SELL = "sell"    # 전량 청산


class ProposedOrder(BaseModel):
    """종목별 제안. *수량* 환산은 사이징이 — 결정 단계는 방향·논지·예산만 낸다."""
    model_config = {"extra": "forbid"}

    code: str
    action: OrderAction
    risk_budget: float = Field(default=0.0, ge=0.0, le=1.0)   # 거래당 위험 예산(0~1)
    thesis: str = ""                                          # entry_thesis 논지
    invalidation_price: float | None = None                  # 논지 무효가(exits 논지무효)


class DeciderOutput(BaseModel):
    """결정 규칙의 사이클 출력. 신규+보유 동적 관리 제안."""
    model_config = {"extra": "forbid"}

    orders: list[ProposedOrder] = Field(default_factory=list)
    notes: str = ""
