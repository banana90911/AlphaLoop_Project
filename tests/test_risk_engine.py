"""리스크 엔진 코어 — 하드 한도·서킷브레이커·안전정지 (risk/risk_engine, 05-risk 5-1·A)."""
import pytest

from config.settings import load_params
from risk.risk_engine import (
    Account,
    MarketState,
    OrderProposal,
    Position,
    StockStatus,
    breakers_tripped,
    can_auto_resume,
    check_new_buy,
    daily_loss_pct,
    detect_anomaly,
    drawdown_pct,
    safety_check,
    screen_cycle,
    screen_order,
)


@pytest.fixture
def params():
    return load_params("risk_params")


def _acc(cash, positions=None, start=10_000_000, peak=10_000_000):
    return Account(start_capital=start, cash=cash,
                   positions=positions or [], peak_equity=peak)


def test_equity_and_daily_loss():
    acc = _acc(5_000_000, [Position("005930", "반도체", 100, 50_000)])
    assert acc.equity == 10_000_000              # 현금 500만 + 주식 500만
    assert daily_loss_pct(acc) == 0.0
    acc.positions[0].last_price = 40_000         # 주식 400만 → 평가 900만
    assert daily_loss_pct(acc) == pytest.approx(-0.10)


def test_drawdown_uses_peak():
    acc = _acc(0, [Position("A", "S", 100, 8_000)], peak=10_000_000)  # 평가 80만?
    # 평가액 800,000, 고점 10,000,000 → -92%
    assert drawdown_pct(acc) == pytest.approx(0.08 / 1.0 - 1.0, abs=1e-9)


def test_daily_loss_breaker_trips(params):
    acc = _acc(0, [Position("A", "S", 100, 95_000)])   # 평가 950만, 시작 1000만 → -5%
    assert "daily_loss" in breakers_tripped(acc, params)


def test_no_breaker_when_flat(params):
    acc = _acc(10_000_000)
    assert breakers_tripped(acc, params) == set()


def test_drawdown_breaker_trips(params):
    # 고점 1000만 대비 -25% (drawdown_halt_pct=0.20 초과)
    acc = _acc(7_500_000, start=20_000_000, peak=10_000_000)
    assert "drawdown" in breakers_tripped(acc, params)


def test_per_name_hard_limit(params):
    acc = _acc(10_000_000)                       # 자본 1000만, 하드 25%=250만
    assert check_new_buy(acc, "A", "반도체", 2_000_000, params)        # 200만 OK
    v = check_new_buy(acc, "A", "반도체", 3_000_000, params)           # 300만 초과
    assert not v and "종목당" in v.reason


def test_sector_limit_aggregates(params):
    # 같은 섹터 보유 250만 + 신규 100만 = 350만 > 섹터 30%(300만)
    acc = _acc(7_500_000, [Position("A", "반도체", 100, 25_000)])
    v = check_new_buy(acc, "B", "반도체", 1_000_000, params)
    assert not v and "섹터" in v.reason


def test_gross_exposure_limit(params):
    # 보유 900만 + 신규 200만 = 1100만 > 총노출 100%(자본 1000만)
    acc = _acc(1_000_000, [Position("A", "반도체", 100, 90_000)])
    v = check_new_buy(acc, "B", "바이오", 2_000_000, params)
    assert not v and "총노출" in v.reason


def test_safety_check():
    acc = _acc(10_000_000)
    assert safety_check(acc, prices_ok=True, balance_matches=True)
    assert not safety_check(acc, prices_ok=False, balance_matches=True)
    assert not safety_check(acc, prices_ok=True, balance_matches=False)


# ── A.1 검사 순서 ──
def test_screen_cycle_proceed(params):
    d = screen_cycle(MarketState(), _acc(10_000_000), params)
    assert d.action == "proceed"


def test_screen_cycle_halt_on_balance(params):
    # 잔고 불일치가 최우선(시장 마비보다 먼저)
    d = screen_cycle(MarketState(balance_ok=False, halted=True), _acc(10_000_000), params)
    assert d.action == "halt" and "잔고" in d.reason


def test_screen_cycle_skip_on_market_halt(params):
    d = screen_cycle(MarketState(halted=True), _acc(10_000_000), params)
    assert d.action == "skip"


def test_screen_cycle_new_blocked_on_breaker(params):
    acc = _acc(0, [Position("A", "S", 100, 95_000)])    # -5% 일일손실
    d = screen_cycle(MarketState(), acc, params)
    assert d.action == "new_blocked" and "daily_loss" in d.reason


def test_screen_order_blocks_suspended(params):
    acc = _acc(10_000_000)
    v = screen_order(acc, "A", "반도체", 1_000_000, StockStatus(suspended=True), params)
    assert not v and "거래정지" in v.reason


def test_screen_order_hardrule_first(params):
    # 하드룰(종목당) 위반이 종목상태보다 먼저 잡힘
    acc = _acc(10_000_000)
    v = screen_order(acc, "A", "반도체", 3_000_000, StockStatus(vi=True), params)
    assert not v and "종목당" in v.reason


def test_screen_order_liquidity(params):
    acc = _acc(10_000_000)
    v = screen_order(acc, "A", "반도체", 1_000_000, StockStatus(), params, liquidity_ok=False)
    assert not v and "유동성" in v.reason


def test_screen_order_ok(params):
    acc = _acc(10_000_000)
    assert screen_order(acc, "A", "반도체", 1_000_000, StockStatus(), params)


# ── A.2 재개 ──
def test_auto_resume_daily_loss():
    assert can_auto_resume("daily_loss")


def test_auto_resume_drawdown_needs_recovery():
    assert can_auto_resume("drawdown", recovered_to_half=True)
    assert not can_auto_resume("drawdown", recovered_to_half=True, deadlock=True)
    assert not can_auto_resume("drawdown", recovered_to_half=False)


def test_safe_stop_needs_human():
    assert not can_auto_resume("safe_stop")
    assert not can_auto_resume("balance_mismatch")


# ── A.3 모델 이상행동 ──
def test_anomaly_normal_ok(params):
    acc = _acc(10_000_000)
    props = [OrderProposal("A", "buy", 1_000_000), OrderProposal("B", "buy", 1_000_000)]
    assert detect_anomaly(props, acc, params)


def test_anomaly_single_order_too_big(params):
    acc = _acc(10_000_000)
    props = [OrderProposal("A", "buy", 4_000_000)]   # 40% > 30%
    v = detect_anomaly(props, acc, params)
    assert not v and "단일주문" in v.reason


def test_anomaly_order_flood(params):
    acc = _acc(10_000_000)                            # 1000만 → 5건 한도
    props = [OrderProposal(f"S{i}", "buy", 500_000) for i in range(6)]
    v = detect_anomaly(props, acc, params)
    assert not v and "폭주" in v.reason


def test_anomaly_flood_scales_with_capital(params):
    acc = _acc(20_000_000)                            # 2000만 → 10건 한도
    props = [OrderProposal(f"S{i}", "buy", 500_000) for i in range(8)]
    assert detect_anomaly(props, acc, params)         # 8건 OK


def test_anomaly_flood_floor_small_capital(params):
    """소액(비례식<1건)이라도 동시보유 상한(floor=max_positions)까지 정상 진입 허용(A.3)."""
    acc = _acc(1_000_000)                             # 100만 → 비례식 0.5건, floor=max_positions
    mp = params["limits"]["max_positions"]
    assert detect_anomaly([OrderProposal("A", "buy", 200_000)], acc, params)   # 단일 진입 OK
    props = [OrderProposal(f"S{i}", "buy", 100_000) for i in range(mp)]
    assert detect_anomaly(props, acc, params)         # floor만큼 동시 진입 OK
    props = [OrderProposal(f"S{i}", "buy", 50_000) for i in range(mp + 1)]
    v = detect_anomaly(props, acc, params)
    assert not v and "폭주" in v.reason               # floor 초과는 폭주


def test_anomaly_buy_sell_conflict(params):
    acc = _acc(10_000_000)
    props = [OrderProposal("A", "buy", 1_000_000), OrderProposal("A", "sell", 1_000_000)]
    v = detect_anomaly(props, acc, params)
    assert not v and "충돌" in v.reason
