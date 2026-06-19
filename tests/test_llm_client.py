"""LLM 클라이언트 파싱 로직 — 추출·검증·repair 폴백 (agents/llm_client, 11-3.3).

call_json 실호출은 네트워크·비용이라 단위테스트에서 제외(파싱·폴백 순수 로직만 검증).
"""
from agents.llm_client import _extract_json, _parse
from core.schemas import CatalystView, MarketView


def test_extract_codeblock():
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_bare_object():
    assert _extract_json('설명 {"a": 1} 끝') == '{"a": 1}'


def test_extract_array():
    assert _extract_json('```\n[1, 2]\n```') == '[1, 2]'


def test_parse_ok():
    t = '{"code": "005930", "view": "bullish", "confidence": 0.8}'
    obj, st = _parse(t, CatalystView)
    assert st == "ok" and obj.view is MarketView.BULLISH


def test_parse_repair_trailing_comma():
    t = '{"code": "A", "view": "neutral", "confidence": 0.5,}'   # 끝 콤마
    obj, st = _parse(t, CatalystView)
    assert st == "repaired" and obj is not None


def test_parse_fail_on_garbage():
    obj, st = _parse("그냥 텍스트", CatalystView)
    assert obj is None and st == "failed"


def test_parse_fail_on_schema_violation():
    # JSON은 유효하나 confidence 범위 위반 → repair로도 못 살림
    obj, st = _parse('{"code": "A", "view": "bullish", "confidence": 9}', CatalystView)
    assert obj is None and st == "failed"
