"""
description:        포지션 사이징 — 동일가중(기본) / 변동성타깃팅+켈리(대안)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from math import floor

from config.settings import load_params

_EPS = 1e-9  # 부동소수점 경계 보정(40.0이 39.999…로 잘려 1주 줄어드는 것 방지)


def _ifloor(x: float) -> int:
    """부동소수점 오차를 보정한 floor."""
    return floor(x + _EPS)


def risk_pct(conviction: float, pmin: float, pmax: float) -> float:
    """거래당 위험 비율: conviction 0→pmin, 1→pmax."""
    return pmin + conviction * (pmax - pmin)


def volatility_target_qty(capital: float, rpct: float, entry: float, stop: float) -> int:
    """변동성 타깃팅 수량 = floor(자본·risk_pct / |진입가−손절가|)."""
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0 or entry <= 0:
        return 0
    return _ifloor(capital * rpct / risk_per_share)


def equal_weight_qty(capital: float, entry: float, n_target: int) -> int:
    """동일가중 수량 = floor(자본 / 목표 보유 수 / 주가)."""
    if entry <= 0 or n_target <= 0:
        return 0
    return _ifloor(capital / n_target / entry)


def kelly_fraction(p: float, b: float, k: float) -> float:
    """프랙셔널 켈리 분수 f = k·(p − (1−p)/b)."""
    if b <= 0:
        return 0.0
    return k * (p - (1 - p) / b)


def kelly_cap_qty(
    capital: float, entry: float, p: float, b: float, k: float, n: int, n_min: int
) -> int | None:
    """켈리 천장 수량을 계산한다. 표본 부족(n<n_min)이거나 f≤0이면 None(상한 없음)."""
    if n < n_min:
        return None
    if entry <= 0:
        return 0
    f = kelly_fraction(p, b, k)
    if f <= 0:
        return None
    return _ifloor(capital * f / entry)


def position_qty(
    capital: float,
    entry: float,
    stop: float,
    conviction: float,
    *,
    n_target: int | None = None,
    p: float | None = None,
    b: float | None = None,
    n: int = 0,
    extra_caps: tuple[int, ...] = (),
    params: dict | None = None,
) -> int:
    """최종 수량 = min(방식별 기본 수량, 켈리 천장, extra_caps)."""
    rp = params or load_params("risk_params")
    s = rp["sizing"]
    if s.get("method", "equal_weight") == "equal_weight":
        base = equal_weight_qty(capital, entry,
                                n_target or int(rp["limits"]["max_positions"]))
        return max(0, min([base, *extra_caps]))
    rpct = risk_pct(conviction, s["risk_pct_min"], s["risk_pct_max"])
    candidates = [volatility_target_qty(capital, rpct, entry, stop), *extra_caps]
    if p is not None and b is not None:
        qk = kelly_cap_qty(capital, entry, p, b, s["kelly_fraction"], n, s["kelly_min_trades"])
        if qk is not None:
            candidates.append(qk)
    return max(0, min(candidates))
