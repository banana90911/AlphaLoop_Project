"""포트폴리오 배분 · 교체매매 · 레짐별 노출 (risk/portfolio, 05-risk 5-2).

종목 한 건이 아니라 *계좌 전체의 모양*을 다룬다. 세 차원:
- 동시 보유 목표 = 자본 규모의 연속 함수(소액일수록 적게, 거래비용·정수주 제약)
- 섹터 분산 = 한 섹터 동시 보유 2종목 이내(섹터 통째 동시하락 위험 차단)
- 레짐별 총노출 = 시장 국면이 우호적이면 상한까지, 악화되면 현금↑(모멘텀 크래시 회피)
- 교체매매 = 슬롯이 꽉 찼는데 더 나은 후보가 오면 최약체와 교체(자본을 더 나은 곳으로)

전부 결정론 순수 함수(LLM 미관여). 임계는 config/risk_params.toml [portfolio](튜닝).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Holding:
    """포트폴리오 판단용 보유 요약. edge=비용 차감 기대엣지(또는 conviction)."""
    code: str
    sector: str
    edge: float


def target_positions(capital: float, params: dict) -> int:
    """자본 규모 → 목표 동시 보유 종목 수(구간 함수, 5-2). 소액일수록 적게."""
    p = params["portfolio"]
    for cap_max, n in p["position_tiers"]:
        if capital < cap_max:
            return n
    return p["position_tiers_max"]


def target_exposure(regime_score: float, params: dict) -> float:
    """레짐 우호도(0=악화 ~ 1=우호) → 목표 총노출(min_exposure ~ 1.0). 5-2 레짐별 노출."""
    lo = params["portfolio"]["min_exposure"]
    s = min(max(regime_score, 0.0), 1.0)
    return lo + s * (1.0 - lo)


def sector_count(holdings: list[Holding], sector: str) -> int:
    return sum(1 for h in holdings if h.sector == sector)


def can_add_sector(holdings: list[Holding], sector: str, params: dict) -> bool:
    """한 섹터 동시 보유 상한(기본 2) 이내면 True. 초과면 교체매매 검토 대상(5-2)."""
    return sector_count(holdings, sector) < params["portfolio"]["max_per_sector"]


def weakest(holdings: list[Holding]) -> Holding | None:
    """기대엣지가 가장 낮은 보유(교체 후보). 비면 None."""
    return min(holdings, key=lambda h: h.edge) if holdings else None


def rotation_candidate(
    holdings: list[Holding], target_count: int, cand_edge: float, params: dict
) -> Holding | None:
    """슬롯이 꽉 찼을 때 신규 후보가 최약체보다 *뚜렷이* 강하면 교체 대상(최약체)을 반환.

    빈 슬롯이 있으면(보유 < 목표) 교체 불필요 → None(그냥 신규 추가). 5-2 교체매매.
    """
    if len(holdings) < target_count:
        return None
    w = weakest(holdings)
    if w is None:
        return None
    margin = params["portfolio"]["rotation_margin"]
    return w if cand_edge > w.edge + margin else None
