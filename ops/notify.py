"""Discord 웹훅 알림 (10-ops 10.4).

폰·데스크톱·웹에서 같은 알림을 받으므로 기기 하나를 잃어도 확인할 수 있다.

**조기 경보로 보내는 것**: 일일 배치 실패, 모의계좌 만료 임박, KIS 호출 한도 90%
도달, 디스크 80% 사용, 메모리 스왑 발생·DB 연결 실패, 백업 실패, 시계 동기화 오차,
데이터 출처 폴백 발생.

**시크릿은 절대 싣지 않는다.** 계좌번호는 마스킹하고(`810XXXXX`), App Key·토큰은
메시지에 넣지 않는다 — 알림 채널은 로그보다 새 나가기 쉽다(10-ops 10.5).

알림 실패가 매매를 멈춰서는 안 된다. 여기서 나는 예외는 삼키고 로그만 남긴다.

미구현 — 아래 함수는 전부 뼈대다.
"""
from __future__ import annotations

# 심각도 — 정지·사고는 즉시 확인이 필요하고, 경보는 하루 안에 보면 된다.
LEVELS = ("info", "warning", "critical")


def send(message: str, *, level: str = "info", title: str | None = None) -> bool:
    """Discord 웹훅으로 한 건 보낸다. 반환: 성공 여부(실패해도 예외를 올리지 않는다)."""
    raise NotImplementedError


def notify_safe_stop(cause: str, cycle_id: str | None = None) -> None:
    """전체 정지 알림 — 사람이 풀어줘야 하는 상태다(05-risk 5.4)."""
    raise NotImplementedError


def notify_ingest_failure(target_table: str, reason: str) -> None:
    """일일 배치 실패 알림. 조용히 지나가면 그날 판단 전체가 낡은 데이터 위에서 이뤄진다."""
    raise NotImplementedError
