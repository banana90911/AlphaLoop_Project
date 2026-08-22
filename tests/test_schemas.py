"""결정 입출력 스키마 — 전체 검증·범위 (core/schemas)."""
import pytest
from pydantic import ValidationError

from core.schemas import DeciderOutput, OrderAction, ProposedOrder


def test_proposed_order_defaults():
    o = ProposedOrder(code="005930", action="buy")
    assert o.action is OrderAction.BUY
    assert o.risk_budget == 0.0 and o.invalidation_price is None


def test_risk_budget_out_of_range_rejected():
    with pytest.raises(ValidationError):
        ProposedOrder(code="A", action="buy", risk_budget=1.5)


def test_unknown_action_rejected():
    with pytest.raises(ValidationError):
        ProposedOrder(code="A", action="moon")


def test_extra_field_forbidden():
    # 부분 파싱 금지의 반대 — 모르는 필드도 거부(스키마 엄격)
    with pytest.raises(ValidationError):
        ProposedOrder(code="A", action="buy", unexpected=1)


def test_decider_output_roundtrip():
    out = DeciderOutput(orders=[ProposedOrder(code="A", action="buy", risk_budget=0.5)])
    d = out.model_dump()
    assert DeciderOutput(**d).orders[0].code == "A"
