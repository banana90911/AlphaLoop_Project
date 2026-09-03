"""
description:        거래비용·세금·슬리피지 단일 모델 (백테스트·실거래 공통)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date

from config.settings import load_params


class CostError(RuntimeError):
    """해당 시장·날짜의 세율을 찾을 수 없음."""


def _sell_tax_rate(trade_date: date, market: str, schedule: list[dict]) -> float:
    """effective_from ≤ trade_date 인 같은 시장 행 중 최신 세율을 반환한다."""
    applicable = [
        r for r in schedule
        if r["market"] == market and date.fromisoformat(r["effective_from"]) <= trade_date
    ]
    if not applicable:
        raise CostError(f"{market} {trade_date} 세율 없음 — tax_rates.toml 확인")
    return max(applicable, key=lambda r: r["effective_from"])["rate"]


def trade_cost(
    price: float,
    qty: int,
    side: str,
    market: str,
    trade_date: date,
    *,
    stress: float = 1.0,
    params: dict | None = None,
) -> dict[str, float]:
    """한 거래의 비용(commission·tax·slippage·total)을 분해해 반환한다."""
    if side not in ("buy", "sell"):
        raise ValueError(f"side는 buy|sell: {side!r}")
    p = params or load_params("tax_rates")
    value = price * qty
    commission = value * p["brokerage"]["rate"]
    slippage = value * p["slippage"]["rate"] * stress
    tax = value * _sell_tax_rate(trade_date, market, p["sell_tax"]) if side == "sell" else 0.0
    return {
        "commission": commission,
        "tax": tax,
        "slippage": slippage,
        "total": commission + tax + slippage,
    }


def round_trip_cost(
    entry: float,
    exit_price: float,
    qty: int,
    market: str,
    entry_date: date,
    exit_date: date,
    *,
    stress: float = 1.0,
    params: dict | None = None,
) -> float:
    """매수→매도 왕복 총비용(원)을 반환한다."""
    buy = trade_cost(entry, qty, "buy", market, entry_date, stress=stress, params=params)
    sell = trade_cost(exit_price, qty, "sell", market, exit_date, stress=stress, params=params)
    return buy["total"] + sell["total"]


def entry_edge(
    entry: float,
    stop: float,
    qty: int,
    market: str,
    trade_date: date,
    *,
    reward_r: float,
    stress: float = 1.0,
    params: dict | None = None,
) -> dict[str, float]:
    """진입 1건의 기대이익·왕복비용·순엣지를 계산한다 — 06-sizing 6.1 무거래 판정.

    진입 시점에는 실제 청산가를 알 수 없으므로, 규칙이 정한 첫 이정표인
    `+reward_r × R`(본전 상향 지점) 도달을 기대이익의 대용으로 쓴다. 이 값이
    왕복 거래비용을 못 넘으면 그 거래는 이겨도 남는 것이 없다.

    반환: expected_gain·estimated_cost·net_edge(원) + reward_risk_ratio(비용 뺀 R 배수).
    """
    zero = {"expected_gain": 0.0, "estimated_cost": 0.0, "net_edge": 0.0,
            "reward_risk_ratio": 0.0}
    risk_per_share = entry - stop
    if risk_per_share <= 0 or qty <= 0 or entry <= 0:
        return zero
    target = entry + reward_r * risk_per_share
    gain = (target - entry) * qty
    cost = round_trip_cost(entry, target, qty, market, trade_date, trade_date,
                           stress=stress, params=params)
    net = gain - cost
    return {
        "expected_gain": gain,
        "estimated_cost": cost,
        "net_edge": net,
        # 비용을 뺀 뒤 몇 R이 남는가 — 설계 3-2가 요구하는 "수수료 빼고 남는 엣지"
        "reward_risk_ratio": net / (risk_per_share * qty),
    }
