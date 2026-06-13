"""거래비용·세금·슬리피지 단일 모델 (백테스트·실거래 공통, 03-arch core / 10-1).

세 요소: ① 증권거래세(매도 시, 시장·날짜별 — config/tax_rates.toml), ② 증권사 수수료
(매수·매도 각각), ③ 슬리피지(체결 불리, 양방향 추정).

날짜별 세율을 쓰는 이유: 거래세가 해마다 바뀌어, 백테스트는 *그 거래일 시점의 세율*로
재현해야 미래정보 누설이 없다(03-arch 3.3). 실거래도 같은 함수를 써 모드 갭을 없앤다.
"""
from __future__ import annotations

from datetime import date

from config.settings import load_params


class CostError(RuntimeError):
    """해당 시장·날짜의 세율을 찾을 수 없음."""


def _sell_tax_rate(trade_date: date, market: str, schedule: list[dict]) -> float:
    """effective_from ≤ trade_date 인 같은 시장 행 중 최신 세율."""
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
    """한 거래의 비용 분해. side='buy'|'sell'. stress=슬리피지 배수(워크포워드 2배 스트레스).

    반환: commission·tax·slippage·total (원). 매수엔 거래세 없음.
    """
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
    """매수→매도 왕복 총비용(원). 미니 주문 hurdle 판정·net 수익 계산용(05-risk §125)."""
    buy = trade_cost(entry, qty, "buy", market, entry_date, stress=stress, params=params)
    sell = trade_cost(exit_price, qty, "sell", market, exit_date, stress=stress, params=params)
    return buy["total"] + sell["total"]
