"""
description:        Heartbeat — 조용한 실패 차단 (외부 모니터링 ping)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import logging

import requests

from config.settings import get_settings

log = logging.getLogger(__name__)

TIMEOUT_S = 5


def _ping(suffix: str = "", payload: str | None = None) -> bool:
    """모니터링에 신호를 보낸다(실패해도 예외를 올리지 않는다)."""
    base = get_settings().healthcheck_url
    if not base:
        log.debug("healthcheck_url 미설정 — ping 생략(%s)", suffix or "success")
        return False
    try:
        r = requests.post(base.rstrip("/") + suffix, data=(payload or "")[:2000],
                          timeout=TIMEOUT_S)
        return r.status_code < 400
    except Exception as e:
        log.warning("heartbeat ping 실패 %s: %s", type(e).__name__, e)
        return False


def ping_start() -> bool:
    """사이클 시작 신호를 보낸다."""
    return _ping("/start")


def ping_success(detail: str = "") -> bool:
    """정상 종료 신호를 보낸다(안 오면 모니터링이 알람)."""
    return _ping("", detail)


def ping_failure(detail: str = "") -> bool:
    """사이클 실패를 즉시 알린다."""
    return _ping("/fail", detail)


def ping_safe_stop(cause: str) -> bool:
    """안전 정지 신호를 별도 주소로 즉시 보낸다(없으면 /fail로 대체)."""
    s = get_settings()
    url = getattr(s, "healthcheck_safestop_url", "") or ""
    if not url:
        return ping_failure(f"SafeStop: {cause}")
    try:
        r = requests.post(url, data=f"SafeStop: {cause}"[:2000], timeout=TIMEOUT_S)
        return r.status_code < 400
    except Exception as e:
        log.warning("safestop ping 실패 %s: %s", type(e).__name__, e)
        return False
