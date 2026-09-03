"""
description:        결정 입출력 스키마 (pydantic v2 전체 검증)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class OrderAction(StrEnum):
    BUY = "buy"      # 신규 진입
    ADD = "add"      # 추가 매수
    HOLD = "hold"    # 유지
    TRIM = "trim"    # 부분 청산
    SELL = "sell"    # 전량 청산


class ProposedOrder(BaseModel):
    """종목별 제안 — 방향·논지·예산만 낸다(수량 환산은 사이징이 한다)."""
    model_config = {"extra": "forbid"}

    code: str
    action: OrderAction
    risk_budget: float = Field(default=0.0, ge=0.0, le=1.0)   # 거래당 위험 예산(0~1)
    thesis: str = ""                                          # entry_thesis 논지
    invalidation_price: float | None = None                  # 논지 무효가(exits 논지무효)


class DeciderOutput(BaseModel):
    """결정 규칙의 사이클 출력 — 신규+보유 동적 관리 제안 목록."""
    model_config = {"extra": "forbid"}

    orders: list[ProposedOrder] = Field(default_factory=list)
    notes: str = ""
