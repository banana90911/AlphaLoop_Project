"""
description:        리스크 엔진 코어 — 하드 한도·서킷브레이커·안전정지
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

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
    safety_check,
    screen_cycle,
    screen_order,
)


@pytest.fixture
def params():
    return load_params("risk_params")


def _acc(cash, positions=None, start=10_000_000):
    return Account(start_capital=start, cash=cash, positions=positions or [])


def test_equity_and_daily_loss():
    acc = _acc(5_000_000, [Position("005930", 100, 50_000)])
    assert acc.equity == 10_000_000              # 현금 500만 + 주식 500만
    assert daily_loss_pct(acc) == 0.0
    acc.positions[0].last_price = 40_000         # 주식 400만 → 평가 900만
    assert daily_loss_pct(acc) == pytest.approx(-0.10)


def test_daily_loss_breaker_trips(params):
    acc = _acc(0, [Position("A", 100, 95_000)])   # 평가 950만, 시작 1000만 → -5%
    assert "daily_loss" in breakers_tripped(acc, params)


def test_no_breaker_when_flat(params):
    acc = _acc(10_000_000)
    assert breakers_tripped(acc, params) == set()


def test_no_per_name_limit(params):
    """종목당 한도는 두지 않는다(05-risk 5.1) — 총노출 안이면 한 종목에 얼마든 담긴다."""
    acc = _acc(10_000_000)
    assert check_new_buy(acc, "A", 1_000_000, params)
    assert check_new_buy(acc, "A", 5_000_000, params)        # 자본의 50%도 통과


def test_no_concentration_limit_beyond_gross(params):
    """섹터 한도도 두지 않는다(03-arch 3-2 — 종목→업종 소스 부재)."""
    acc = _acc(8_000_000, [Position("A", 100, 20_000)])
    assert check_new_buy(acc, "B", 1_200_000, params)


def test_gross_exposure_limit(params):
    # 보유 950만 + 신규 100만 = 1050만 > 총노출 100%(자본 1000만) → 총노출 사유.
    acc = _acc(500_000, [Position("A", 100, 95_000)])
    v = check_new_buy(acc, "B", 1_000_000, params)
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
    # 보유 불일치가 최우선(시장 마비보다 먼저). 현금 불일치는 사고가 아니라 입출금이라
    # 따로 다룬다 — 아래 test_screen_cycle_proceeds_on_ordinary_flow 참고.
    d = screen_cycle(MarketState(balance_ok=False, halted=True), _acc(10_000_000), params)
    assert d.action == "halt" and "보유 불일치" in d.reason


def test_screen_cycle_skip_on_market_halt(params):
    d = screen_cycle(MarketState(halted=True), _acc(10_000_000), params)
    assert d.action == "skip"


def test_screen_cycle_new_blocked_on_breaker(params):
    acc = _acc(0, [Position("A", 100, 95_000)])    # -5% 일일손실
    d = screen_cycle(MarketState(), acc, params)
    assert d.action == "new_blocked" and "daily_loss" in d.reason


def test_screen_order_blocks_suspended(params):
    acc = _acc(10_000_000)
    v = screen_order(acc, "A", 1_000_000, StockStatus(suspended=True), params)
    assert not v and "거래정지" in v.reason


def test_screen_order_hardrule_first(params):
    # 하드룰(총노출) 위반이 종목상태보다 먼저 잡힘
    acc = _acc(10_000_000)
    v = screen_order(acc, "A", 11_000_000, StockStatus(vi=True), params)
    assert not v and "총노출" in v.reason


def test_screen_order_ok(params):
    acc = _acc(10_000_000)
    assert screen_order(acc, "A", 1_000_000, StockStatus(), params)


# ── A.2 재개 ──
def test_auto_resume_daily_loss():
    assert can_auto_resume("daily_loss")


def test_auto_resume_api_error_needs_recovery():
    assert can_auto_resume("api_error", error_rate_ok=True)
    assert not can_auto_resume("api_error", error_rate_ok=False)


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
    # 실효 임계 = max(max_positions, 비례식) — 동시보유 상한을 넘는 건수가 폭주
    acc = _acc(10_000_000)
    mp = params["limits"]["max_positions"]
    props = [OrderProposal(f"S{i}", "buy", 100_000) for i in range(mp + 1)]
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


# ── 외부 현금흐름(입출금) — 기준선 평행이동과 2단 잔고 대조 ──────────────────
def test_daily_loss_pct_unchanged_without_flow():
    """회귀 방지 — net_external_flow=0.0이면 예전 식과 1원도 다르지 않다."""
    acc = _acc(5_000_000, [Position("005930", 100, 40_000)])   # 평가 900만 / 기준 1000만
    assert acc.net_external_flow == 0.0
    assert acc.baseline == acc.start_capital
    assert daily_loss_pct(acc) == pytest.approx(acc.equity / acc.start_capital - 1.0)
    assert daily_loss_pct(acc) == pytest.approx(-0.10)


def test_baseline_shift_deposit_reveals_real_loss(params):
    """케이스 A — 입금이 진짜 −4% 손실을 가리지 못하게 기준선을 올린다(05-risk 5.2)."""
    # 어제 총자산 1000만 → 밤에 +200만 입금 → 오늘 −4% 하락해 1152만
    acc = Account(start_capital=10_000_000, cash=11_520_000, net_external_flow=2_000_000)
    assert acc.equity / acc.start_capital - 1.0 == pytest.approx(0.152)   # 안 옮기면 +15.2%
    assert daily_loss_pct(acc) == pytest.approx(-0.04)                    # 옮기면 −4.0%
    assert "daily_loss" in breakers_tripped(acc, params)                  # 정상 발동


def test_baseline_shift_withdrawal_avoids_false_trip(params):
    """케이스 B — 출금이 가짜 −30%를 만들어 하루를 통째로 버리는 일을 막는다."""
    # 어제 총자산 1000만 → 밤에 −300만 출금 → 오늘 손익 없이 700만
    acc = Account(start_capital=10_000_000, cash=7_000_000, net_external_flow=-3_000_000)
    assert acc.equity / acc.start_capital - 1.0 == pytest.approx(-0.30)   # 안 옮기면 −30%
    assert daily_loss_pct(acc) == pytest.approx(0.0)                      # 옮기면 0%
    assert not breakers_tripped(acc, params)                              # 가짜 발동 없음


def test_screen_cycle_halt_on_negative_cash(params):
    """미수(예수금 음수)는 즉시 안전 정지(05-risk 5.1)."""
    d = screen_cycle(MarketState(cash_negative=True), _acc(10_000_000), params)
    assert d.action == "halt" and "미수" in d.reason
    assert (d.check, d.result) == ("balanceSync", "safeStop")


def test_screen_cycle_halt_on_large_outflow(params):
    """유출 전 총자산의 50%를 넘게 빠져나가면 사고로 보고 사람을 부른다."""
    # 유출 후 400만 남음 + 유출 600만 = 유출 전 1000만 → 60% > 50%
    d = screen_cycle(MarketState(cash_residual=-6_000_000), _acc(4_000_000), params)
    assert d.action == "halt" and "유출" in d.reason
    assert (d.check, d.result) == ("balanceSync", "safeStop")


def test_screen_cycle_proceeds_on_ordinary_flow(params):
    """평범한 입출금은 기록만 하고 매매를 그대로 진행한다 — 이번 설계의 핵심 이득."""
    # 200만 입금 → 예수금도 기준선도 같이 200만 올라 손익률은 그대로 0%
    acc = Account(start_capital=10_000_000, cash=12_000_000, net_external_flow=2_000_000)
    assert screen_cycle(MarketState(cash_residual=2_000_000), acc, params).action == "proceed"
    # 유출 쪽도 50% 미만이면 통과 (900만 남음 + 100만 유출 = 1000만 중 10%)
    out = Account(start_capital=10_000_000, cash=9_000_000, net_external_flow=-1_000_000)
    assert screen_cycle(MarketState(cash_residual=-1_000_000), out, params).action == "proceed"


def test_holdings_mismatch_checked_before_cash(params):
    """검사 순서 — 보유 불일치(1-a)가 현금 검사(1-b)보다 먼저 걸린다."""
    d = screen_cycle(
        MarketState(balance_ok=False, cash_negative=True, cash_residual=-9_000_000),
        _acc(1_000_000), params,
    )
    assert d.action == "halt" and "보유 불일치" in d.reason
