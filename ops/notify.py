"""
description:        Discord 웹훅 알림 (조기 경보 + 시크릿 마스킹)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import logging
import re

import requests

from config.settings import get_settings

log = logging.getLogger(__name__)

TIMEOUT_S = 5
MAX_LEN = 1900          # Discord 본문 2000자 제한 - 제목·꾸밈 여유

# 심각도 → 표시. 정지·사고는 즉시 확인이 필요하고, 경보는 하루 안에 보면 된다.
LEVELS = ("info", "warning", "critical")
_MARK = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}

# 메시지에 섞여 들어갈 수 있는 시크릿 형태 — 보내기 전에 지운다.
_SECRET_PATTERNS = (
    re.compile(r"\b\d{8}-?\d{2}\b"),                 # 계좌번호 8-2
    re.compile(r"\bPS[A-Za-z0-9]{16,}\b"),           # KIS App Key
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]+", re.I),  # 토큰
)


def _mask(text: str) -> str:
    """시크릿으로 보이는 토막을 가린다."""
    for pat in _SECRET_PATTERNS:
        text = pat.sub("***", text)
    return text


def send(message: str, *, level: str = "info", title: str | None = None) -> bool:
    """Discord 웹훅으로 한 건 보낸다(실패해도 예외를 올리지 않는다). 반환: 성공 여부."""
    if level not in LEVELS:
        level = "info"
    url = get_settings().discord_webhook_url
    if not url:
        log.warning("discord_webhook_url 미설정 — 알림 생략: %s", title or message[:60])
        return False
    head = f"{_MARK[level]} **{title}**\n" if title else f"{_MARK[level]} "
    body = _mask(message)[:MAX_LEN]
    try:
        r = requests.post(url, json={"content": head + body}, timeout=TIMEOUT_S)
        if r.status_code >= 400:
            log.warning("알림 전송 실패 HTTP %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:                    # 알림 실패가 매매를 멈추면 안 된다
        log.warning("알림 전송 예외 %s: %s", type(e).__name__, e)
        return False


def notify_safe_stop(cause: str, cycle_id: str | None = None) -> bool:
    """전체 정지 알림을 보낸다(사람이 풀어줘야 하는 상태)."""
    where = f"\n사이클: `{cycle_id}`" if cycle_id else ""
    return send(
        f"매매를 전체 정지했습니다.\n원인: {cause}{where}\n\n"
        "신규 주문이 차단됩니다(보유 청산은 계속 돕니다). "
        "잔고 불일치·데이터 오류는 확인 후 사람이 직접 해제해야 합니다.",
        level="critical", title="SafeStop 발생",
    )


def notify_cash_flow(
    flow_id: str, amount: float, *, expected: float, actual: float,
    kind: str = "unknown", cycle_id: str | None = None,
) -> bool:
    """외부 현금흐름 감지 알림. 차단이 아니라 "기록했고 그대로 진행했다"는 통지다."""
    direction = "입금" if amount >= 0 else "출금"
    where = f"\n감지 사이클: `{cycle_id}`" if cycle_id else ""
    return send(
        f"{direction} {abs(amount):,.0f}원으로 보이는 현금 변동을 감지했습니다.\n"
        f"기대 예수금: {expected:,.0f}원 / 실제 예수금: {actual:,.0f}원\n"
        f"분류: `{kind}`{where}\n\n"
        "보유 종목·수량은 일치하므로 **매매는 그대로 계속됩니다.** "
        "서킷브레이커 기준선은 이 금액만큼 자동으로 옮겼습니다.\n"
        "라벨만 나중에 붙여주세요:\n"
        f"`python -m ops.cashflow confirm --id {flow_id} --kind deposit`\n"
        "(배당이면 `--kind dividend` — 입금은 수익률에서 빼고 배당은 수익으로 잡습니다.)",
        level="info", title="외부 현금흐름 감지",
    )


def notify_ingest_failure(target_table: str, reason: str) -> bool:
    """일일 배치 실패 알림을 보낸다(신선도 검사가 사이클을 막는다는 안내 포함)."""
    return send(
        f"표: `{target_table}`\n사유: {reason}\n\n"
        "오늘 사이클은 데이터 신선도 검사에서 멈춥니다. 배치를 다시 돌리세요 "
        "(`--resume`으로 못 받은 종목만 재시도).",
        level="warning", title="일일 배치 실패",
    )


def notify_cycle_failure(cycle_id: str, step: int | None, reason: str) -> bool:
    """사이클이 도중에 죽었을 때 어느 단계에서 멈췄는지 알린다."""
    at = f"{step}단계" if step else "단계 미상"
    return send(f"사이클: `{cycle_id}`\n멈춘 곳: {at}\n사유: {reason}",
                level="warning", title="사이클 실패")
