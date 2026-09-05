"""
description:        수정주가·신선도·이상치 방어 (data/corrections, 04-data 4.1·4.3).
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from core.timeutils import now_utc
from data.corrections import (
    PRICE_LIMIT,
    adjust_stop_for_action,
    apply_adjustments,
    check_freshness,
    drop_stale_rows,
    flag_price_jumps,
)
from memory import journal


def _bars(closes, start=date(2026, 1, 5)):
    idx = [start + timedelta(days=i) for i in range(len(closes))]
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "volume": [1000.0] * len(closes)},
        index=idx,
    )


# ── ① 수정주가 ───────────────────────────────────────────────────
def test_no_actions_leaves_prices_untouched():
    bars = _bars([100.0, 110.0, 120.0])
    out = apply_adjustments(bars, pd.DataFrame())
    assert list(out["close"]) == [100.0, 110.0, 120.0]
    assert not out["is_adjusted"].any()


def test_bonus_scales_only_prior_days():
    # 1/8 권리락(배수 0.5) → 그 전날까지만 반값으로, 당일부터는 그대로
    bars = _bars([100.0, 100.0, 50.0, 50.0])          # 1/5,1/6,1/7,1/8
    actions = pd.DataFrame([{"ex_date": date(2026, 1, 7), "price_factor": 0.5}])
    out = apply_adjustments(bars, actions)
    assert list(out["close"]) == [50.0, 50.0, 50.0, 50.0]   # 단차가 사라진다
    assert out["is_adjusted"].tolist() == [True, True, False, False]


def test_volume_moves_opposite_to_price():
    bars = _bars([100.0, 50.0])
    actions = pd.DataFrame([{"ex_date": date(2026, 1, 6), "price_factor": 0.5}])
    out = apply_adjustments(bars, actions)
    assert out["volume"].iloc[0] == 2000.0    # 가격 반값 → 수량 두 배
    assert out["volume"].iloc[1] == 1000.0


def test_unknown_factor_is_skipped():
    # 유상증자처럼 배수를 못 구하면 건드리지 않는다(잘못 고치는 것보다 낫다)
    bars = _bars([100.0, 100.0])
    actions = pd.DataFrame([{"ex_date": date(2026, 1, 6), "price_factor": None}])
    out = apply_adjustments(bars, actions)
    assert list(out["close"]) == [100.0, 100.0]


def test_multiple_actions_compound():
    bars = _bars([100.0, 100.0, 100.0])
    actions = pd.DataFrame([
        {"ex_date": date(2026, 1, 6), "price_factor": 0.5},
        {"ex_date": date(2026, 1, 7), "price_factor": 0.5},
    ])
    out = apply_adjustments(bars, actions)
    assert out["close"].iloc[0] == pytest.approx(25.0)    # 0.5 × 0.5
    assert out["close"].iloc[1] == pytest.approx(50.0)
    assert out["close"].iloc[2] == 100.0


def test_stop_moves_with_price_factor():
    # 안 내리면 기준가가 떨어진 순간 멀쩡한 종목이 손절로 털린다
    assert adjust_stop_for_action(10_000, 0.5) == 5_000


def test_stop_unchanged_when_factor_unknown():
    assert adjust_stop_for_action(10_000, 0) == 10_000
    assert adjust_stop_for_action(10_000, None) == 10_000


# ── ③ 이상치 ─────────────────────────────────────────────────────
def test_flags_jump_beyond_price_limit():
    bars = _bars([100.0, 100.0, 200.0])       # +100% — 제한폭 초과
    flags = flag_price_jumps(bars)
    assert flags.tolist() == [False, False, True]


def test_normal_moves_not_flagged():
    bars = _bars([100.0, 125.0])              # +25% — 제한폭 안
    assert not flag_price_jumps(bars).any()
    assert PRICE_LIMIT == 0.30


def test_drop_stale_rows_removes_halted_symbol():
    bars = _bars([100.0, 101.0], start=date(2026, 1, 5))
    assert drop_stale_rows(bars, asof=date(2026, 3, 1)).empty      # 두 달 전 → 버린다
    assert not drop_stale_rows(bars, asof=date(2026, 1, 8)).empty  # 사흘 전 → 유지


# ── ② 신선도 (DB) ────────────────────────────────────────────────
def _run(conn, table, status, finished_hours_ago=0.0):
    started = now_utc() - timedelta(hours=finished_hours_ago + 0.1)
    journal.record_ingest_run(
        conn, run_id=f"r_{table}", target_table=table, source="test", status=status,
        started_at=started, range_start=date(2026, 8, 28), range_end=date(2026, 8, 28),
        error_message="테스트" if status != "ok" else None,
    )
    if finished_hours_ago:      # record_ingest_run은 now로 찍으므로 과거로 되돌린다
        conn.execute(
            'UPDATE ingest_runs SET finished_date_time=%s WHERE run_id=%s',
            (now_utc() - timedelta(hours=finished_hours_ago), f"r_{table}"),
        )
        conn.commit()


def test_freshness_passes_when_batch_ok(conn):
    for t in ("daily_bars", "daily_scores"):
        _run(conn, t, "ok")
    ok, reason = check_freshness(conn, trade_date=date(2026, 8, 28))
    assert ok and reason == ""


def test_freshness_fails_without_record(conn):
    ok, reason = check_freshness(conn, trade_date=date(2026, 8, 28))
    assert not ok and "기록 없음" in reason


def test_freshness_rejects_partial(conn):
    # 일부 종목이 빠진 채 백분위를 매기면 그 종목들이 조용히 후보에서 사라진다
    _run(conn, "daily_bars", "partial")
    _run(conn, "daily_scores", "ok")
    ok, reason = check_freshness(conn, trade_date=date(2026, 8, 28))
    assert not ok and "partial" in reason


def test_freshness_rejects_stale_batch(conn):
    for t in ("daily_bars", "daily_scores"):
        _run(conn, t, "ok", finished_hours_ago=30)
    ok, reason = check_freshness(conn, trade_date=date(2026, 8, 28))
    assert not ok and "초과" in reason
