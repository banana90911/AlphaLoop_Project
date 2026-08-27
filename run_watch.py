"""보유 감시 진입점 (장중 30분 간격 — 03-arch 3.1).

정기 사이클 사이에도 보유는 위험에 노출돼 있다. 감시는 **이미 걸어둔 손절이 제대로
작동하는지만** 본다 — 신규 진입도, 손절선 상향도 하지 않는다.

두 가지를 한다.
1. 스톱지정가(22)가 실제로 KIS에 등록돼 있는지 확인하고, 빠진 것이 있으면 등록한다.
2. 개장 갭이 지정가를 건너뛰어 미체결로 남은 보유를 감지하면 시장가로 강제 정리한다
   (`exec.exits.detect_stop_gaps`가 판정, 정리는 사이클의 청산 경로가 한다).

**트레일링과 부분 청산은 하지 않는다.** 청산 규칙이 일봉 종가 기준으로 정의돼 있어,
장중 고점에 손절을 붙이면 노이즈에 더 자주 털린다(03-arch 3.1).

보유 종목만 조회하므로 호출 비용은 사실상 0이다.

미구현 — 아래 함수는 전부 뼈대다.
"""
from __future__ import annotations

import argparse


def sync_resident_stops(conn, *, broker) -> list[str]:
    """`Positions`의 open 보유 중 상주 스톱이 없는 것을 찾아 등록한다. 반환: 등록한 주문 id.

    `ActiveStopOrderId`가 비어 있으면 손절 없이 방치된 포지션이라는 뜻이다(07-model).
    KIS 미체결 주문 조회와 대조해, 우리 장부에만 있고 KIS에 없는 스톱도 다시 건다.
    """
    raise NotImplementedError


def sweep_stop_gaps(conn, *, broker) -> list[str]:
    """손절 구멍(현재가 ≤ 손절가인데 아직 보유)을 시장가로 강제 정리한다. 반환: 청산 주문 id.

    잔고 동기화를 먼저 해야 한다 — 그래야 밤사이 자동 체결된 스톱이 반영돼 *이미 팔린
    종목을 손절 구멍으로 오인*하지 않는다(`exec.exits.detect_stop_gaps` docstring).
    """
    raise NotImplementedError


def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaLoop 보유 감시")
    parser.parse_args()
    raise NotImplementedError


if __name__ == "__main__":
    main()
