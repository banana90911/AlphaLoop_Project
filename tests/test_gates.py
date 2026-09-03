"""
description:        게이트 입력 조립 — 잔고 대조·종목 상태·시장 상태
author:             siheon jung
created date:       2026/08/30
last modified date: 2026/08/30
remarks:
"""

from datetime import date, timedelta

import pytest

from core.timeutils import now_utc
from memory import journal
from pipeline import gates


def _quote(**over) -> dict:
    base = {
        "stck_prpr": "8000", "stck_mxpr": "10000", "stck_llam": "6000",
        "iscd_stat_cls_code": "00", "temp_stop_yn": "N", "vi_cls_code": "N",
    }
    return {"output": {**base, **over}}


# ── 종목 상태 판정 (05-risk 5.2 검사 7) ─────────────────────────────
def test_normal_quote_is_tradable():
    st = gates.stock_status(_quote())
    assert not st.limit_lock and not st.suspended and not st.vi
    assert st.block_reason == ""


def test_limit_up_and_down():
    assert gates.stock_status(_quote(stck_prpr="10000")).block_reason == "limitUp"
    assert gates.stock_status(_quote(stck_prpr="6000")).block_reason == "limitDown"


@pytest.mark.parametrize("code", ["51", "52", "53", "58"])
def test_suspended_codes_block(code):
    assert gates.stock_status(_quote(iscd_stat_cls_code=code)).suspended


def test_caution_code_does_not_block():
    # 54(투자주의)는 경고·위험보다 약한 단계라 진입을 막지 않는다
    assert not gates.stock_status(_quote(iscd_stat_cls_code="54")).suspended


def test_overheated_and_vi():
    assert gates.stock_status(_quote(iscd_stat_cls_code="59")).block_reason == "overheated"
    assert gates.stock_status(_quote(vi_cls_code="Y")).block_reason == "vi"


def test_unknown_flag_value_blocks():
    # 모르는 값을 정상으로 넘기지 않는다(보수적 차단)
    assert gates.stock_status(_quote(vi_cls_code="3")).vi


def test_temp_stop_marks_suspended():
    assert gates.stock_status(_quote(temp_stop_yn="Y")).suspended


def test_block_reason_priority():
    # 여러 사유가 겹쳐도 검사 순서대로 첫 하나만 남긴다
    st = gates.stock_status(_quote(stck_prpr="10000", iscd_stat_cls_code="58"))
    assert st.block_reason == "limitUp"


def test_flat_output_shape_is_accepted():
    # output으로 감싸지 않은 dict도 그대로 읽는다
    assert gates.stock_status({"stck_prpr": "10000", "stck_mxpr": "10000"}).limit_up


# ── 현재가 조회 실패 격리 ────────────────────────────────────────────
class _FlakyClient:
    def __init__(self, bad: set[str]):
        self.bad = bad

    def get_price(self, code):
        if code in self.bad:
            raise RuntimeError("KIS 5xx")
        return _quote()


def test_fetch_quotes_drops_failures():
    got = gates.fetch_quotes(_FlakyClient({"B"}), ["A", "B", "C"])
    assert set(got) == {"A", "C"}          # 실패한 종목은 '모름'으로 빠진다


def test_fetch_quotes_carries_last_price():
    got = gates.fetch_quotes(_FlakyClient(set()), ["A"])
    assert got["A"].last_price == 8000.0   # 진입가·손절가 확정에 쓸 현재가
    assert got["A"].status.block_reason == ""


def test_quote_without_price_is_none():
    q = gates.quote_of({"output": {"stck_prpr": "0", "stck_mxpr": "10000"}})
    assert q.last_price is None            # 0은 값이 아니라 결측이다


# ── 잔고 대조 (05-risk 5.2 검사 1 선행 게이트) ───────────────────────
def _open_position(conn, code: str, qty: int) -> None:
    journal.upsert_entry_position(
        conn, cycle_id="C1", symbol_id=code, add_quantity=qty, fill_price=1000.0,
        entry_decision_id=None, current_stop_price=900.0, initial_stop_price=900.0,
    )


def test_balance_matches(conn):
    _open_position(conn, "005930", 10)
    ok, why = gates.reconcile_balance(conn, {"005930": 10})
    assert ok and why == ""


def test_balance_quantity_mismatch(conn):
    _open_position(conn, "005930", 10)
    ok, why = gates.reconcile_balance(conn, {"005930": 7})
    assert not ok
    assert "장부10≠실잔고7" in why


def test_balance_missing_in_kis(conn):
    _open_position(conn, "005930", 10)
    ok, why = gates.reconcile_balance(conn, {})
    assert not ok and "005930" in why


def test_balance_extra_in_kis(conn):
    ok, why = gates.reconcile_balance(conn, {"000660": 3})
    assert not ok and "장부0≠실잔고3" in why


def test_empty_both_sides_matches(conn):
    ok, _ = gates.reconcile_balance(conn, {})
    assert ok


# ── 시장 상태 조립 ──────────────────────────────────────────────────
def _fresh_ingest(conn, day: date, table: str, status: str = "ok") -> None:
    journal.record_ingest_run(
        conn, run_id=f"{day:%Y%m%d}_{table}", target_table=table, source="test",
        status=status, started_at=now_utc(), range_start=day, range_end=day,
    )


def test_market_state_ok_on_trading_day(conn):
    day = date(2026, 8, 28)                # 금요일 — 거래일
    for t in ("DailyBars", "DailyScores"):
        _fresh_ingest(conn, day, t)
    state, notes = gates.build_market_state(
        conn, kis_holdings={}, trade_date=day, check_data=True,
    )
    assert state.balance_ok and state.prices_ok and not state.halted
    assert notes == ""


def test_market_state_flags_holiday(conn):
    day = date(2026, 8, 29)                # 토요일 — 휴장
    state, notes = gates.build_market_state(
        conn, trade_date=day, check_data=False,
    )
    assert state.halted and "거래일이 아니다" in notes


def test_market_state_flags_stale_data(conn):
    day = date(2026, 8, 28)
    state, notes = gates.build_market_state(
        conn, trade_date=day, check_data=True,   # IngestRuns 없음 → 신선도 실패
    )
    assert not state.prices_ok and "배치 기록 없음" in notes


def test_market_state_flags_failed_ingest(conn):
    day = date(2026, 8, 28)
    _fresh_ingest(conn, day, "DailyBars", status="failed")
    _fresh_ingest(conn, day, "DailyScores")
    state, notes = gates.build_market_state(conn, trade_date=day, check_data=True)
    assert not state.prices_ok and "DailyBars 배치 failed" in notes


def test_market_state_flags_balance_mismatch(conn):
    day = date(2026, 8, 28)
    _open_position(conn, "005930", 10)
    state, notes = gates.build_market_state(
        conn, kis_holdings={"005930": 4}, trade_date=day, check_data=False,
    )
    assert not state.balance_ok and "잔고 불일치" in notes


def test_market_state_skips_balance_when_not_given(conn):
    # kis_holdings=None이면 대조를 하지 않는다(조회 실패 시 잔고 정상으로 오판 방지는 호출 측 책임)
    state, _ = gates.build_market_state(conn, trade_date=date(2026, 8, 28),
                                        check_data=False)
    assert state.balance_ok


def test_sidecar_and_market_cb_are_not_detected(conn):
    # 조회 경로가 없어 항상 False — 설계가 요구하는 감지가 아직 없다는 사실을 고정한다
    state, _ = gates.build_market_state(conn, trade_date=date(2026, 8, 28),
                                        check_data=False)
    assert state.sidecar is False and state.market_cb is False


def test_freshness_window_expires(conn):
    day = date(2026, 8, 28)
    for t in ("DailyBars", "DailyScores"):
        _fresh_ingest(conn, day, t)
    # FinishedDateTime을 과거로 밀어 신선도 창을 벗어나게 한다
    conn.execute(
        'UPDATE "IngestRuns" SET "FinishedDateTime"=%s', (now_utc() - timedelta(days=3),)
    )
    conn.commit()
    state, notes = gates.build_market_state(conn, trade_date=day, check_data=True)
    assert not state.prices_ok and "시간 전" in notes
