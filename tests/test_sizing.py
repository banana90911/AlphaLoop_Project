"""포지션 사이징 — 동일가중·변동성 타깃팅·켈리 천장 (06-sizing, 09-eval 9.4 ①).

라이브에서 켈리가 켜지길 기다릴 필요 없이 가짜 (p,b,n)으로 지금 검증한다.
"""
from risk import sizing

_KELLY = {"risk_pct_min": 0.0075, "risk_pct_max": 0.025,
          "kelly_fraction": 0.5, "kelly_min_trades": 20}
# 기본 사이징(동일가중) — 목표 보유 수는 limits.max_positions
_PARAMS = {"sizing": {"method": "equal_weight", **_KELLY}, "limits": {"max_positions": 20}}
# 변동성 타깃팅 경로 검증용
_VOL = {"sizing": {"method": "volatility_target", **_KELLY}, "limits": {"max_positions": 20}}


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


def test_kelly_none_when_edge_negative():
    # p=0.3, b=1 → f < 0 → None(천장 미적용). 0을 돌려주면 매매 중단 스위치가 된다(6.1)
    assert sizing.kelly_cap_qty(10_000_000, 50_000, 0.3, 1.0, 0.5, n=30, n_min=20) is None


def test_kelly_cap_value():
    # p=0.6, b=2 → f = 0.5*(0.6 - 0.4/2) = 0.5*0.4 = 0.2 → 200만/5만 = 40주
    assert sizing.kelly_cap_qty(10_000_000, 50_000, 0.6, 2.0, 0.5, n=30, n_min=20) == 40


def test_equal_weight_qty():
    # 자본 1000만 / 목표 20종목 / 주가 5만 = 10주
    assert sizing.equal_weight_qty(10_000_000, 50_000, 20) == 10


def test_position_qty_default_is_equal_weight():
    qty = sizing.position_qty(10_000_000, 50_000, 45_000, conviction=0.7, params=_PARAMS)
    assert qty == 10                                 # 1000만/20/5만


def test_position_qty_kelly_negative_edge_keeps_base():
    # f<=0이면 천장 미적용 — 기본 수량이 그대로 남아야(매매가 멈추면 안 된다)
    qty = sizing.position_qty(
        10_000_000, 50_000, 45_000, conviction=0.7,
        p=0.3, b=1.0, n=100, params=_PARAMS,
    )
    assert qty == 10


def test_position_qty_takes_min_with_kelly_active():
    # 변동성 100주 vs 켈리 40주 → 40주
    qty = sizing.position_qty(
        10_000_000, 50_000, 49_000, conviction=0.0,  # risk_pct=0.0075 → 75000/1000=75주
        p=0.6, b=2.0, n=30, params=_VOL,
    )
    # vol=floor(1000만*0.0075/1000)=75, kelly=40 → min=40
    assert qty == 40


def test_position_qty_kelly_dormant_uses_volatility():
    # n<20 → 켈리 휴면, 변동성만
    qty = sizing.position_qty(
        10_000_000, 50_000, 49_000, conviction=1.0,  # risk_pct=0.025 → 25만/1000=250주
        p=0.6, b=2.0, n=5, params=_VOL,
    )
    assert qty == 250


def test_position_qty_extra_caps_apply():
    qty = sizing.position_qty(
        10_000_000, 50_000, 49_000, conviction=1.0,  # 변동성 250주
        extra_caps=(30,), params=_VOL,               # 유동성/총노출 한도 30주
    )
    assert qty == 30
