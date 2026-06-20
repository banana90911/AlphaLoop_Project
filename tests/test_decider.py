"""결정 에이전트 — 프롬프트 빌드·반대의견 게이트 (agents/decider). 실호출 제외."""
import pytest

from agents.code_decider import Candidate
from agents.decider import _apply_dissent_gate, _build_user
from config.settings import load_params
from core.schemas import DeciderOutput, OrderAction, ProposedOrder


@pytest.fixture
def params():
    return load_params("risk_params")


def test_build_user_includes_constraints():
    cands = [Candidate("005930", 0.8, 0.9), Candidate("000660", 0.6, None)]
    txt = _build_user(cands, ["035720"], cash=3_000_000, equity=10_000_000)
    assert "005930: 스크리너 0.80, 촉매 0.90" in txt
    assert "000660: 스크리너 0.60, 촉매 없음" in txt
    assert "보유] 035720" in txt
    assert "가용현금 3,000,000원" in txt


def test_dissent_gate_blocks_weak_buy(params):
    out = DeciderOutput(orders=[
        ProposedOrder(code="A", action=OrderAction.BUY, dissent_addressed=0.4),   # 반대 우세
        ProposedOrder(code="B", action=OrderAction.BUY, dissent_addressed=0.8),   # 통과
    ])
    kept = _apply_dissent_gate(out, params["decision"]["dissent_min"])
    codes = [o.code for o in kept.orders]
    assert codes == ["B"]


def test_dissent_gate_keeps_sell_regardless(params):
    # 청산(sell/trim)은 반대의견 게이트 무관(리스크 축소는 막지 않음)
    out = DeciderOutput(orders=[
        ProposedOrder(code="A", action=OrderAction.SELL, dissent_addressed=0.1),
    ])
    kept = _apply_dissent_gate(out, params["decision"]["dissent_min"])
    assert len(kept.orders) == 1
