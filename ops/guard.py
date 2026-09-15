"""
description:        진입점 최상위 예외 처리 (조용한 실패 차단)
author:             siheon jung
created date:       2026/09/15
remarks:            2026-09-15 실측: run_cycle이 잔고조회(KIS 초당 제한)에서 죽었는데
                    Cycles 행이 만들어지기 전이라 Discord도 heartbeat도 울리지 않았다.
                    로그 파일에만 traceback이 남아 그날 밤까지 아무도 몰랐다.
                    10-ops 10.4가 막으려던 것이 정작 가장 이른 실패 구간에 없었다.
"""

import logging
import traceback
from collections.abc import Iterator
from contextlib import contextmanager

from ops import heartbeat, notify

log = logging.getLogger(__name__)


@contextmanager
def guard(name: str) -> Iterator[None]:
    """진입점을 감싸, 어떤 예외든 알린 뒤 그대로 다시 올린다.

    사이클·배치는 DB에 기록을 남기기 전에도 죽을 수 있다 — KIS 조회, DB 연결, 설정
    로드. 그 구간에서 죽으면 DB에도 알림에도 흔적이 없어 로그를 직접 열기 전에는
    알 수가 없다. 여기가 마지막 그물이다.

    `SystemExit`은 통과시킨다. 거래일이 아니라 스스로 끝내는 정상 경로이고, 실패로
    끝내는 쪽(`run_daily_ingest`의 실패 단계)은 이미 자기 알림을 보낸 뒤다.
    """
    try:
        yield
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        log.exception("%s 진입점이 죽었다", name)
        # 알림이 또 실패해도 원래 예외를 덮지 않는다 — 진단은 traceback이 우선이다.
        try:
            notify.notify_entrypoint_crash(name, detail, traceback.format_exc())
            heartbeat.ping_failure(f"{name} crash: {detail}")
        except Exception:
            log.exception("죽음을 알리는 것마저 실패했다")
        raise
