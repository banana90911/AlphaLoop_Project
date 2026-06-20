"""결정 흐름 조립 — A/B/C 모드 분기 (pipeline/decision, 10-3.1).

A 모드는 LLM 미사용이라 직접 검증, B·C는 catalyst/decider를 monkeypatch(실호출 회피).
"""
import pytest

from agents.catalyst import NewsBundle
from agents.code_decider import Candidate
from config.settings import load_params
from core.schemas import CatalystView, DeciderOutput, OrderAction, ProposedOrder
from pipeline import decision


@pytest.fixture
def params():
    return load_params("risk_params")


def _cands():
    return [Candidate("005930", 0.9, None), Candidate("000660", 0.3, None)]


def test_invalid_mode(params):
    with pytest.raises(ValueError, match="mode"):
        decision.run_decision([], [], [], cash=0, equity=0, params=params, mode="X")


def test_mode_a_no_catalyst_call(params, monkeypatch):
    # A: catalyst 호출되면 실패하게 해 미호출을 검증
    monkeypatch.setattr(catalyst_target(), "analyze",
                        lambda *_: (_ for _ in ()).throw(AssertionError("A는 촉매 미사용")))
    out = decision.run_decision(_cands(), [], [], cash=1e7, equity=1e7,
                                params=params, mode="A")
    codes = [o.code for o in out.orders if o.action is OrderAction.BUY]
    assert codes == ["005930"]            # screen 0.9만 τ 통과, 0.3은 무거래


def test_mode_b_uses_catalyst(params, monkeypatch):
    # B: 촉매가 000660을 강세로 끌어올리면 매수 후보가 됨
    monkeypatch.setattr(catalyst_target(), "analyze",
                        lambda *_: [CatalystView(code="000660", view="bullish", confidence=1.0)])
    # 000660: screen 0.3·촉매 1.0 → (0.6*0.3+0.4*1.0)=0.58 < 0.6 여전히 미달, 005930만
    out = decision.run_decision(_cands(), [NewsBundle("000660", "하이닉스", ["호재"])],
                                [], cash=1e7, equity=1e7, params=params, mode="B")
    assert any(o.code == "005930" for o in out.orders)


def test_mode_c_calls_decider(params, monkeypatch):
    monkeypatch.setattr(catalyst_target(), "analyze", lambda *_: [])
    sentinel = DeciderOutput(orders=[ProposedOrder(code="Z", action=OrderAction.BUY)])
    monkeypatch.setattr(decision.decider, "decide", lambda *a, **k: sentinel)
    out = decision.run_decision(_cands(), [], [], cash=1e7, equity=1e7,
                                params=params, mode="C")
    assert out.orders[0].code == "Z"      # decider(LLM) 경로 사용


def catalyst_target():
    return decision.catalyst
