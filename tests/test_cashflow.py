"""
description:        외부 현금흐름 감지 — 잔차 판별·CashFlows 적재·기준선 평행이동
author:             siheon jung
created date:       2026/09/02
last modified date: 2026/09/02
remarks:            매매는 주식과 현금을 같이 움직인다. 주식이 맞는데 현금만
                    어긋난 것이 곧 입출금이라는 게 이 파일이 지키는 성질이다.
"""

from datetime import date

import pytest

from memory import journal
from ops import cashflow
from pipeline import cycle

_DAY = date(2026, 9, 2)


def _cycle(conn, cid="C1"):
    journal.create_cycle(conn, cid, trade_date=_DAY, mode="paper")
    return cid


# ── 판별 정책(순수 함수) ────────────────────────────────────────────────────
def test_absorb_threshold_floor_and_ratio():
    """흡수 임계 = max(1,000원, 자본의 0.01%)."""
    assert cashflow.absorb_threshold(1_000_000) == 1_000.0     # 0.01%=100 → 하한 1,000
    assert cashflow.absorb_threshold(100_000_000) == 10_000.0  # 0.01%=10,000 > 하한


def test_small_residual_is_absorbed_not_a_transfer():
    """수수료 절사·이자 수준의 잔차는 이체가 아니라 손익으로 흡수한다."""
    r = cashflow.classify_residual(430, 10_000_000, observation_mode=False)
    assert r.absorbed and not r.record and not r.shifts_baseline


def test_small_residual_still_logged_in_observation_mode():
    """관찰 모드에서는 흡수분도 기록만 남긴다 — 임계를 실측으로 확정하기 위해서다."""
    r = cashflow.classify_residual(430, 10_000_000, observation_mode=True)
    assert r.absorbed and r.record and r.kind == "fee"
    assert not r.shifts_baseline and not r.alert     # 기준선도 안 옮기고 안 울린다


def test_large_residual_becomes_external_flow():
    """임계를 넘는 잔차는 외부 흐름으로 기록하고 기준선을 옮긴다."""
    r = cashflow.classify_residual(2_000_000, 10_000_000, observation_mode=True)
    assert r.record and not r.absorbed and r.kind == "unknown"
    assert r.shifts_baseline and r.alert             # 자본 1% 초과라 알림도 울린다


def test_observation_mode_stays_quiet_below_alert_threshold():
    """관찰 모드는 자본 1% 미만이면 기록만 하고 Discord를 안 울린다."""
    r = cashflow.classify_residual(50_000, 10_000_000, observation_mode=True)
    assert r.record and r.shifts_baseline and not r.alert


def test_signature_confirms_kind_only_when_enabled():
    """금액 서명(입금 ...777)은 기본 비활성이고, 켜야 Kind가 자동 확정된다."""
    off = cashflow.classify_residual(2_000_777, 10_000_000, signature_enabled=False)
    assert off.kind == "unknown" and off.source == "residual"
    on = cashflow.classify_residual(2_000_777, 10_000_000, signature_enabled=True)
    assert on.kind == "deposit" and on.source == "signature"
    out = cashflow.classify_residual(-2_000_555, 10_000_000, signature_enabled=True)
    assert out.kind == "withdrawal"


# ── 기대 예수금·잔차 (DB) ───────────────────────────────────────────────────
def test_expected_cash_is_none_without_prior_snapshot(conn):
    """첫 사이클엔 비교 기준이 없으니 잔차도 없다."""
    assert journal.expected_cash(conn) is None


def test_expected_cash_follows_fills(conn):
    """기대 예수금 = 직전 예수금 + 매도 순수취 − 매수 순지급."""
    _cycle(conn)
    journal.record_account_snapshot(
        conn, cycle_id="C1", cash=5_000_000, position_value=0,
        total_asset=5_000_000, trade_date=_DAY,
    )
    conn.execute(
        'INSERT INTO orders(client_order_id,symbol_id,side,purpose,order_type,'
        'order_quantity,filled_quantity,average_fill_price,fee,status,'
        'ordered_date_time,filled_date_time,mode) VALUES'
        "('o1','005930','buy','entry','00',10,10,100000,1500,'filled',now(),now(),'paper'),"
        "('o2','000660','sell','exit','00',5,5,200000,3000,'filled',now(),now(),'paper')"
    )
    conn.commit()
    got = journal.expected_cash(conn, mode="paper")
    # 매수 −(1,000,000+1,500) + 매도 +(1,000,000−3,000) = −4,500
    assert got["expected"] == pytest.approx(5_000_000 - 4_500)


# ── CashFlows 적재·라벨링 ───────────────────────────────────────────────────
def test_record_and_confirm_cash_flow(conn):
    _cycle(conn)
    fid = journal.record_cash_flow(
        conn, "C1", kind="unknown", amount=2_000_000, source="residual",
        expected=5_000_000, actual=7_000_000, mode="paper",
    )
    row = journal.load_cash_flows(conn, status="unconfirmed")[0]
    assert row["flow_id"] == fid and row["status"] == "unconfirmed"

    assert journal.confirm_cash_flow(conn, fid, kind="deposit", by="cli")
    row = journal.load_cash_flows(conn)[0]
    assert (row["kind"], row["status"], row["confirmed_by"]) == ("deposit", "confirmed", "cli")

    # 다시 부르면 정정(배당을 입금으로 잘못 잡았다가 바로잡는 경로)
    assert journal.confirm_cash_flow(conn, fid, kind="dividend", by="cli")
    assert journal.load_cash_flows(conn)[0]["status"] == "reclassified"


def test_confirm_rejects_unknown_kind(conn):
    _cycle(conn)
    fid = journal.record_cash_flow(
        conn, "C1", kind="unknown", amount=1.0, source="residual",
        expected=0, actual=1, mode="paper",
    )
    with pytest.raises(ValueError):
        journal.confirm_cash_flow(conn, fid, kind="배당금")


def test_dividend_does_not_shift_baseline(conn):
    """배당은 수익이라 기준선을 옮기면 안 된다 — 옮기면 수익을 통째로 지운다."""
    _cycle(conn)
    journal.record_cash_flow(conn, "C1", kind="deposit", amount=1_000_000,
                             source="residual", expected=0, actual=0, mode="paper")
    journal.record_cash_flow(conn, "C1", kind="dividend", amount=30_000,
                             source="residual", expected=0, actual=0, mode="paper")
    assert journal.sum_flows_since(conn, None) == pytest.approx(1_000_000)
    assert journal.cumulative_net_flow(conn) == pytest.approx(1_000_000)


# ── 사이클 배선 ─────────────────────────────────────────────────────────────
def test_snapshot_carries_flow_and_twr(conn):
    """스냅샷이 누적 순입금과 TWR 지수를 이어받는다."""
    _cycle(conn, "C1")
    journal.record_account_snapshot(
        conn, cycle_id="C1", cash=10_000_000, position_value=0,
        total_asset=10_000_000, trade_date=_DAY,
    )
    _cycle(conn, "C2")
    # 200만 입금 후 손익 0 → 총자산 1200만이지만 TWR은 1.0 그대로여야 한다
    journal.record_account_snapshot(
        conn, cycle_id="C2", cash=12_000_000, position_value=0,
        total_asset=12_000_000, base_asset=10_000_000,
        net_flow_since_base=2_000_000, flow_this_snapshot=2_000_000, trade_date=_DAY,
    )
    row = journal.last_account_snapshot(conn)
    assert float(row["adjusted_base_asset"]) == 12_000_000
    assert abs(float(row["day_return_percent"])) < 1e-9       # 입금은 수익이 아니다
    assert float(row["cumulative_net_flow"]) == 2_000_000
    assert row["twr_index"] == pytest.approx(1.0)
    # 검산: TotalAsset − CumulativeNetFlow = 누적 순손익
    assert float(row["total_asset"]) - float(row["cumulative_net_flow"]) == 10_000_000


def test_twr_index_tracks_real_gain_after_deposit(conn):
    """입금 뒤 실제로 번 것만 TWR에 잡힌다."""
    _cycle(conn, "C1")
    journal.record_account_snapshot(conn, cycle_id="C1", cash=10_000_000,
                                    position_value=0, total_asset=10_000_000, trade_date=_DAY)
    _cycle(conn, "C2")
    # 200만 입금 + 진짜 10% 수익 → 1,200만 × 1.1 = 1,320만
    journal.record_account_snapshot(
        conn, cycle_id="C2", cash=13_200_000, position_value=0, total_asset=13_200_000,
        base_asset=10_000_000, net_flow_since_base=2_000_000,
        flow_this_snapshot=2_000_000, trade_date=_DAY,
    )
    assert journal.last_account_snapshot(conn)["twr_index"] == pytest.approx(1.10)


def test_cycle_detects_deposit_and_keeps_trading(conn, monkeypatch):
    """입금 한 번에 매매가 며칠 멈추던 문제가 사라졌는지 — 이번 설계의 핵심 이득."""
    from tests.test_cycle import _rich_account, _universe

    cycle.run(conn, market_data=_universe(), account=_rich_account())   # 기준 스냅샷
    acc = _rich_account()
    acc.cash += 2_000_000                                              # 밤사이 200만 입금
    res = cycle.run(conn, market_data=_universe(), account=acc)

    assert res.cycle_action != "halt"                                   # 안 멈춘다
    flows = journal.load_cash_flows(conn)
    assert len(flows) == 1
    assert float(flows[0]["amount"]) == pytest.approx(2_000_000)
    assert flows[0]["kind"] == "unknown" and flows[0]["status"] == "unconfirmed"

    check = conn.execute(
        'SELECT * FROM risk_checks WHERE check_name=%s', ("cashFlow",)
    ).fetchone()
    assert check["result"] == "flowDetected"


def test_cycle_halts_on_massive_outflow(conn):
    """자본 절반 넘게 빠져나가면 사람을 부른다."""
    from tests.test_cycle import _rich_account, _universe

    cycle.run(conn, market_data=_universe(), account=_rich_account())
    acc = _rich_account()
    acc.cash -= 7_000_000                       # 예수금 800만 중 700만이 사라짐
    res = cycle.run(conn, market_data=_universe(), account=acc)
    assert res.cycle_action == "halt"
    assert journal.active_safe_stop(conn) is not None
