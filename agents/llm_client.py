"""Anthropic SDK 래퍼 — JSON 호출·재시도·4단계 폴백 (agents/llm_client, 09 9.2·11-3).

LLM을 부르는 *유일한* 칸(agents/)의 토대. 역할별 모델(config/models.toml)로 JSON 응답을
요청해 pydantic 스키마로 *전체 검증*한다(부분 파싱 금지, 11-3.3). 재시도·파싱 폴백·호출
메타(토큰·parse_status)를 표준화해 catalyst·decider가 그 위에서 분석/결정만 하게 한다.

설계 준수: messages.create 1:1 매핑(09 9.2), 재시도 직접 구현(429·5xx 지수백오프 / 영구
오류 즉시 중단, 11-3.1), JSON 4단계 폴백(코드블록 추출→repair→재호출→실패, 11-3.3).
모델 차등=비용(news=Haiku·decision=Sonnet, models.toml) — claude-api 기본 Opus가 아님.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import json_repair
from anthropic import Anthropic, APIStatusError
from pydantic import BaseModel, ValidationError

from config.settings import get_settings, load_params

_RETRYABLE = {429, 529, 500, 502, 503, 504}
_PERMANENT = {400, 401, 403, 404}
_MAX_ATTEMPTS = 4                       # 지수백오프 1→2→4초 (마지막 시도엔 sleep 없음)


class LLMError(RuntimeError):
    """LLM 호출·파싱 실패 격리용."""


@dataclass
class LLMResult:
    """검증된 객체 + 호출 메타(llm_calls 적재용). parse_status ∈ {ok,repaired,retried,failed}."""
    data: BaseModel
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    parse_status: str


def _client() -> Anthropic:
    key = get_settings().anthropic_api_key
    if not key:
        raise LLMError("ANTHROPIC_API_KEY 미설정(.env)")
    return Anthropic(api_key=key)


def _extract_json(text: str) -> str:
    """마크다운 코드블록 또는 첫 JSON 객체/배열만 추출 (11-3.3 1차)."""
    m = re.search(r"```(?:json)?\s*([\[{].*[\]}])\s*```", text, re.S)
    if m:
        return m.group(1)
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    return m.group(0) if m else text


def _parse[T: BaseModel](text: str, schema: type[T]) -> tuple[T | None, str]:
    """JSON 추출→검증, 실패 시 json_repair 최소 수정(내용 추정 금지). 반환 (객체|None, 상태)."""
    raw = _extract_json(text)
    try:
        return schema.model_validate_json(raw), "ok"
    except ValidationError:
        pass
    try:
        repaired = json_repair.repair_json(raw)
        return schema.model_validate_json(repaired), "repaired"
    except (ValidationError, ValueError):
        return None, "failed"


def _create(client: Anthropic, model: str, max_tokens: int, system: str, user: str):
    """messages.create + 지수백오프 재시도(일시 오류) / 영구 오류 즉시 중단 (11-3.1)."""
    delay = 1.0
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
        except APIStatusError as e:
            if e.status_code in _PERMANENT:
                raise LLMError(f"영구 오류 {e.status_code}(재시도 금지)") from e
            if attempt == _MAX_ATTEMPTS - 1:
                raise LLMError(f"재시도 소진({e.status_code})") from e
            time.sleep(delay)
            delay *= 2
    raise LLMError("재시도 소진")  # 도달 불가(방어)


def call_json(role: str, system: str, user: str, schema: type[BaseModel]) -> LLMResult:
    """역할(news·decision)별 모델로 JSON 응답 요청 → schema 검증 객체 + 메타.

    파싱 실패 시 동일 입력으로 1회 재호출(11-3.3 3차), 그래도 실패면 LLMError.
    호출측(catalyst=분석가는 부분실패 허용, decider=결정자는 사이클 중단)이 예외를 처리한다.
    """
    models = load_params("models")
    model = models["roles"][role]
    max_tokens = models["params"]["max_tokens"]
    client = _client()

    resp = _create(client, model, max_tokens, system, user)
    obj, status = _parse(resp.content[0].text, schema)
    if obj is None:                                    # 3차: 재호출 1회
        resp = _create(client, model, max_tokens, system, user)
        obj, status2 = _parse(resp.content[0].text, schema)
        if obj is None:
            raise LLMError(f"JSON 검증 실패(role={role}, 재호출 후도 실패)")
        status = "retried"

    u = resp.usage
    return LLMResult(
        data=obj, model=model,
        input_tokens=u.input_tokens, output_tokens=u.output_tokens,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        parse_status=status,
    )
