"""백테스트 거래 결과의 켈리 입력 산출 (backtest/seed, 06-sizing 6.1).

DB 적재 함수(`seed_outcomes`)는 없앴다 — **백테스트 결과는 DB에 넣지 않는다**(07-model
공통 규칙). 실거래 표에 백테스트 행이 섞이면 성과 집계가 오염되기 때문이고, 신설계
`Outcomes`에는 백테스트를 구분할 열 자체가 없다. 백테스트 산출물은 파일로 남긴다(09-eval).

남은 `kelly_pb`는 순수 계산이라 DB와 무관하다. 다만 사이징이 동일가중 20종목으로
확정되면서 켈리는 현재 경로에서 빠져 있고(06-sizing), 이 함수는 분석·비교용으로 남는다.
"""
from __future__ import annotations

from backtest.spec_engine import ClosedTrade


def kelly_pb(trades: list[ClosedTrade]) -> tuple[float, float] | None:
    """거래 결과 → (승률 p, 손익비 b). 이익·손실 표본이 둘 다 있어야 산출(없으면 None).

    p = 이긴 거래 비율, b = 평균 이익금액 ÷ 평균 손실금액.
    """
    if not trades:
        return None
    wins = [t.net_pnl for t in trades if t.net_pnl > 0]
    losses = [-t.net_pnl for t in trades if t.net_pnl < 0]
    if not wins or not losses:
        return None
    p = len(wins) / len(trades)
    b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    return (p, b)
