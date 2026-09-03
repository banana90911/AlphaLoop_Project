"""
description:        백테스트 거래 결과의 켈리 입력(승률·손익비) 산출
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from backtest.spec_engine import ClosedTrade


def kelly_pb(trades: list[ClosedTrade]) -> tuple[float, float] | None:
    """거래 결과에서 (승률 p, 손익비 b)를 산출한다(표본 부족 시 None)."""
    if not trades:
        return None
    wins = [t.net_pnl for t in trades if t.net_pnl > 0]
    losses = [-t.net_pnl for t in trades if t.net_pnl < 0]
    if not wins or not losses:
        return None
    p = len(wins) / len(trades)
    b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    return (p, b)
