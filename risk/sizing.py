"""포지션 사이징 — 변동성 타깃팅(메인) + 프랙셔널 켈리(자동활성 상한) (05-risk 5-2).

결정론 코드(LLM 위임 금지). decider는 conviction 입력만 주고, 수량 환산은 여기서 한다.

구조: 변동성 타깃팅이 수량을 *제안*하고, 켈리·하드 한도가 *천장*을 씌운다.
- 변동성 타깃팅: 주가·손절폭만으로 1일차부터 작동(과거 성적 불요).
- 켈리: 관련 칸 청산거래 n ≥ N_min일 때만 천장으로 작동(그 전 휴면 = 상한 없음).
최종 수량 = min(변동성, 켈리천장, 종목당한도, 유동성, 총노출잔여).
"""
from __future__ import annotations

from math import floor

from config.settings import load_params

_EPS = 1e-9  # 부동소수점 경계 보정(40.0이 39.999…로 잘려 1주 줄어드는 것 방지)


def _ifloor(x: float) -> int:
    return floor(x + _EPS)


def conviction_score(
    confidence: float,
    calibration: float,
    contrarian: float,
    thesis_quality: float,
    weights: dict[str, float],
) -> float:
    """conviction 가중합 → [0,1] 클립. w_confidence는 작게(자기신고, §117)."""
    raw = (
        weights["w_confidence"] * confidence
        + weights["w_calibration"] * calibration
        + weights["w_contrarian"] * contrarian
        + weights["w_thesis"] * thesis_quality
    )
    return min(max(raw, 0.0), 1.0)


def risk_pct(conviction: float, pmin: float, pmax: float) -> float:
    """거래당 위험 비율: conviction 0→pmin, 1→pmax (§119)."""
    return pmin + conviction * (pmax - pmin)


def volatility_target_qty(capital: float, rpct: float, entry: float, stop: float) -> int:
    """① 변동성 타깃팅 수량 = floor(자본·risk_pct / |진입가−손절가|)."""
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0 or entry <= 0:
        return 0
    return _ifloor(capital * rpct / risk_per_share)


def kelly_fraction(p: float, b: float, k: float) -> float:
    """프랙셔널 켈리 분수 f = k·(p − (1−p)/b). b는 손익비(평균이익R/평균손실R)."""
    if b <= 0:
        return 0.0
    return k * (p - (1 - p) / b)


def kelly_cap_qty(
    capital: float, entry: float, p: float, b: float, k: float, n: int, n_min: int
) -> int | None:
    """② 켈리 천장 수량. n < n_min이면 None(휴면=상한 없음). f≤0이면 0(무거래 신호)."""
    if n < n_min:
        return None
    if entry <= 0:
        return 0
    f = kelly_fraction(p, b, k)
    if f <= 0:
        return 0
    return _ifloor(capital * f / entry)


def position_qty(
    capital: float,
    entry: float,
    stop: float,
    conviction: float,
    *,
    p: float | None = None,
    b: float | None = None,
    n: int = 0,
    extra_caps: tuple[int, ...] = (),
    params: dict | None = None,
) -> int:
    """③ 최종 수량 = min(변동성, 켈리천장, extra_caps). extra_caps=종목당·유동성·총노출잔여 수량.

    p·b가 주어지고 n≥N_min이면 켈리 천장이 활성, 아니면 변동성만(+extra_caps).
    """
    s = (params or load_params("risk_params"))["sizing"]
    rpct = risk_pct(conviction, s["risk_pct_min"], s["risk_pct_max"])
    candidates = [volatility_target_qty(capital, rpct, entry, stop), *extra_caps]
    if p is not None and b is not None:
        qk = kelly_cap_qty(capital, entry, p, b, s["kelly_fraction"], n, s["kelly_min_trades"])
        if qk is not None:
            candidates.append(qk)
    return max(0, min(candidates))
