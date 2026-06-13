"""포지션 사이징 — 변동성 타깃팅·켈리 천장·휴면 (05-risk 5-2, 10-2 ①).

라이브에서 켈리가 켜지길 기다릴 필요 없이 가짜 (p,b,n)으로 지금 검증한다.
"""
from risk import sizing

_W = {"w_confidence": 0.2, "w_calibration": 0.4, "w_contrarian": 0.2, "w_thesis": 0.2}
_PARAMS = {"sizing": {
    "risk_pct_min": 0.0075, "risk_pct_max": 0.025,
    "kelly_fraction": 0.5, "kelly_min_trades": 20,
}}


def test_conviction_clips_to_unit():
    assert sizing.conviction_score(1, 1, 1, 1, _W) == 1.0
    assert sizing.conviction_score(0, 0, 0, 0, _W) == 0.0
    # 가중합 0.2*0.5 + 0.4*1 + 0 + 0 = 0.5
    assert abs(sizing.conviction_score(0.5, 1.0, 0, 0, _W) - 0.5) < 1e-9


def test_risk_pct_endpoints():
    assert sizing.risk_pct(0.0, 0.0075, 0.025) == 0.0075
    assert sizing.risk_pct(1.0, 0.0075, 0.025) == 0.025


def test_volatility_target_qty():
    # 자본 1000만, 위험 1%, 손절폭 1000원 → 10만/1000 = 100주
    assert sizing.volatility_target_qty(10_000_000, 0.01, 50_000, 49_000) == 100


def test_volatility_target_zero_when_no_stop_gap():
    assert sizing.volatility_target_qty(10_000_000, 0.01, 50_000, 50_000) == 0


def test_kelly_dormant_below_min_trades():
    # n < n_min → None(휴면, 상한 없음)
    assert sizing.kelly_cap_qty(10_000_000, 50_000, 0.6, 2.0, 0.5, n=10, n_min=20) is None


def test_kelly_zero_when_edge_negative():
    # p=0.3, b=1 → f = 0.5*(0.3 - 0.7/1) < 0 → 0(무거래)
    assert sizing.kelly_cap_qty(10_000_000, 50_000, 0.3, 1.0, 0.5, n=30, n_min=20) == 0


def test_kelly_cap_value():
    # p=0.6, b=2 → f = 0.5*(0.6 - 0.4/2) = 0.5*0.4 = 0.2 → 200만/5만 = 40주
    assert sizing.kelly_cap_qty(10_000_000, 50_000, 0.6, 2.0, 0.5, n=30, n_min=20) == 40


def test_position_qty_takes_min_with_kelly_active():
    # 변동성 100주 vs 켈리 40주 → 40주
    qty = sizing.position_qty(
        10_000_000, 50_000, 49_000, conviction=0.0,  # risk_pct=0.0075 → 75000/1000=75주
        p=0.6, b=2.0, n=30, params=_PARAMS,
    )
    # vol=floor(1000만*0.0075/1000)=75, kelly=40 → min=40
    assert qty == 40


def test_position_qty_kelly_dormant_uses_volatility():
    # n<20 → 켈리 휴면, 변동성만
    qty = sizing.position_qty(
        10_000_000, 50_000, 49_000, conviction=1.0,  # risk_pct=0.025 → 25만/1000=250주
        p=0.6, b=2.0, n=5, params=_PARAMS,
    )
    assert qty == 250


def test_position_qty_extra_caps_apply():
    qty = sizing.position_qty(
        10_000_000, 50_000, 49_000, conviction=1.0,  # 변동성 250주
        extra_caps=(30,), params=_PARAMS,            # 종목당/유동성 한도 30주
    )
    assert qty == 30
