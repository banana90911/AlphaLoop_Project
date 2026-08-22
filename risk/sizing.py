"""포지션 사이징 — 동일가중(기본) / 변동성 타깃팅 + 켈리(대안) (06-sizing 6.1).

결정론 코드. 결정 단계는 conviction 입력만 주고, 수량 환산은 여기서 한다.
방식은 config [sizing] method로 고른다.

- equal_weight(기본): 자본 / 목표 보유 수 / 주가. 종목당 비중이 1/N로 고정이라
  과대 베팅이 구조적으로 불가능하므로 켈리 천장을 얹지 않는다(이중 규제).
- volatility_target(대안): 변동성 타깃팅이 수량을 *제안*하고 켈리가 *천장*을 씌운다.
  켈리는 청산 n ≥ N_min이고 분수가 양수일 때만 걸린다(그 외 휴면 = 상한 없음).

최종 수량 = min(기본 수량, 켈리천장, 유동성·총노출잔여 한도).
"""
from __future__ import annotations

from math import floor

from config.settings import load_params

_EPS = 1e-9  # 부동소수점 경계 보정(40.0이 39.999…로 잘려 1주 줄어드는 것 방지)


def _ifloor(x: float) -> int:
    return floor(x + _EPS)


def risk_pct(conviction: float, pmin: float, pmax: float) -> float:
    """거래당 위험 비율: conviction 0→pmin, 1→pmax (§119)."""
    return pmin + conviction * (pmax - pmin)


def volatility_target_qty(capital: float, rpct: float, entry: float, stop: float) -> int:
    """① 변동성 타깃팅 수량 = floor(자본·risk_pct / |진입가−손절가|)."""
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0 or entry <= 0:
        return 0
    return _ifloor(capital * rpct / risk_per_share)


def equal_weight_qty(capital: float, entry: float, n_target: int) -> int:
    """① 동일가중 수량 = floor(자본 / 목표 보유 수 / 주가) (06-sizing 6.1)."""
    if entry <= 0 or n_target <= 0:
        return 0
    return _ifloor(capital / n_target / entry)


def kelly_fraction(p: float, b: float, k: float) -> float:
    """프랙셔널 켈리 분수 f = k·(p − (1−p)/b). b는 손익비(평균이익R/평균손실R)."""
    if b <= 0:
        return 0.0
    return k * (p - (1 - p) / b)


def kelly_cap_qty(
    capital: float, entry: float, p: float, b: float, k: float, n: int, n_min: int
) -> int | None:
    """② 켈리 천장 수량. 천장을 못 씌우는 경우는 None(휴면=상한 없음).

    n < n_min(표본 부족)일 때와 f ≤ 0(실측 엣지가 음수)일 때 모두 None이다. f ≤ 0에서
    0을 돌려주면 최종 min()에 0이 섞여 *수량이 0* = 매매 중단이 되는데, 켈리는 상한이지
    매매 스위치가 아니다(06-sizing 6.1). 실제로 그 해석은 손실 → 켈리 음수 → 진입 차단 →
    표본 정체 → 계속 차단의 자기실현 루프를 만든다.
    """
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
    """③ 최종 수량 = min(기본 수량, 상한들). extra_caps=유동성·총노출잔여 수량.

    기본 수량은 [sizing] method로 갈린다(06-sizing 6.1):
    - equal_weight(기본): 자본 / 목표 보유 수 / 주가. 종목당 비중이 1/N로 고정이라
      과대 베팅이 구조적으로 불가능하므로 켈리 천장을 얹지 않는다(이중 규제).
    - volatility_target: 자본 × 위험비율 / 손절폭. 종목당 크기가 손절폭에 따라
      들쭉날쭉하므로 켈리를 천장으로 씌운다(p·b가 있고 n≥N_min이며 f>0일 때만).

    n_target 미지정 시 목표 보유 수는 [limits] max_positions를 쓴다.
    """
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
