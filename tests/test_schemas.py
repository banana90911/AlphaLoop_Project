"""LLM 입출력 스키마 — 전체 검증·범위·촉매점수 (core/schemas)."""
import pytest
from pydantic import ValidationError

from core.schemas import (
    CatalystView,
    DeciderOutput,
    MarketView,
    OrderAction,
    ProposedOrder,
)


def test_catalyst_valid():
    cv = CatalystView(code="005930", view="bullish", confidence=0.8,
                      key_signals=["실적 서프라이즈"], key_risks=["환율"])
    assert cv.view is MarketView.BULLISH
    assert cv.score() == pytest.approx(0.9)        # 0.5 + 0.5*0.8


def test_catalyst_score_directions():
    assert CatalystView(code="A", view="bearish", confidence=1.0).score() == pytest.approx(0.0)
    assert CatalystView(code="A", view="neutral", confidence=0.3).score() == pytest.approx(0.5)


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        CatalystView(code="A", view="bullish", confidence=1.5)


def test_unknown_view_rejected():
    with pytest.raises(ValidationError):
        CatalystView(code="A", view="moon", confidence=0.5)


def test_extra_field_forbidden():
    # 부분 파싱 금지의 반대 — 모르는 필드도 거부(스키마 엄격)
    with pytest.raises(ValidationError):
        CatalystView(code="A", view="bullish", confidence=0.5, hallucinated=1)


def test_proposed_order_defaults():
    o = ProposedOrder(code="005930", action="buy")
    assert o.action is OrderAction.BUY
    assert o.dissent_addressed == 1.0 and o.risk_budget == 0.0


def test_dissent_range():
    with pytest.raises(ValidationError):
        ProposedOrder(code="A", action="sell", dissent_addressed=2.0)


def test_decider_output_roundtrip():
    out = DeciderOutput(orders=[ProposedOrder(code="A", action="buy", risk_budget=0.5)])
    d = out.model_dump()
    assert DeciderOutput(**d).orders[0].code == "A"
