"""
description:        대시보드 조회 API — 네 영역이 실제로 값을 내는가 (08-dashboard 8.4)
author:             siheon jung
created date:       2026/08/31
last modified date: 2026/08/31
remarks:            매매 코어가 적재한 표를 읽기만 한다. 쓰기 경로는 이 파일에 없다.
"""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core.timeutils import KST, now_utc
from dashboard import api
from memory import journal

_DAY = date(2026, 8, 28)          # 금요일 — 거래일


@pytest.fixture
def client(conn):
    """DB 연결과 출입증 검증을 테스트용으로 갈아끼운 클라이언트."""
    api.app.dependency_overrides[api.db] = lambda: conn
    api.app.dependency_overrides[api.require_token] = lambda: None
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


@pytest.fixture
def guarded_client(conn):
    """출입증 검증을 살려둔 클라이언트(401 확인용)."""
    api.app.dependency_overrides[api.db] = lambda: conn
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


# ── 시드 헬퍼 ────────────────────────────────────────────────────
def _symbol(conn, code: str, name: str, market: str = "KOSPI") -> None:
    journal.upsert_symbols(conn, [{"code": code, "name": name, "market": market}])


def _cycle(conn, cycle_id: str, day: date = _DAY) -> None:
    journal.create_cycle(conn, cycle_id, trade_date=day, mode="paper")


def _order(conn, coid: str, *, code: str, side: str, purpose: str,
           ordered_at: datetime, cycle_id=None, decision_id=None,
           filled: int = 10, price: float = 1000.0) -> None:
    journal.record_order(
        conn, client_order_id=coid, cycle_id=cycle_id, decision_id=decision_id,
        symbol_id=code, side=side, purpose=purpose, order_type="00",
        order_quantity=filled, filled_quantity=filled, order_price=price,
        average_fill_price=price, status="filled", mode="paper",
        ordered_at=ordered_at, filled_at=ordered_at,
    )


# ── 인증 경계 ────────────────────────────────────────────────────
def test_health_needs_no_login(guarded_client):
    assert guarded_client.get("/api/health").json() == {"ok": True}


@pytest.mark.parametrize("path", [
    "/api/account", "/api/equity-curve", "/api/trades", "/api/alerts",
    "/api/benchmark/watchlist",
])
def test_endpoints_require_token(guarded_client, path):
    # 주소를 알아도 로그인 없이는 한 줄도 주지 않는다(08-dashboard 8.5)
    assert guarded_client.get(path).status_code == 401


def test_login_rejects_when_unconfigured(guarded_client, monkeypatch):
    monkeypatch.setattr(api.auth, "is_configured", lambda: False)
    r = guarded_client.post("/api/login", json={"password": "x"})
    assert r.status_code == 503


@pytest.fixture(autouse=True)
def _reset_lockout():
    """잠금 상태는 프로세스 메모리에 남는다 — 테스트끼리 새어 나가지 않게 비운다."""
    api.auth._attempts.clear()
    yield
    api.auth._attempts.clear()


def test_login_sets_httponly_cookie(guarded_client, monkeypatch):
    monkeypatch.setattr(api.auth, "is_configured", lambda: True)
    monkeypatch.setattr(api.auth, "verify_password", lambda p: p == "ok")
    monkeypatch.setattr(api.auth, "issue_token", lambda: "TOKEN")
    r = guarded_client.post("/api/login", json={"password": "ok"})
    assert r.status_code == 200 and r.json()["expires_hours"] == 12
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    # 세션 쿠키여야 한다 — 만료가 박히면 브라우저가 디스크에 저장한다(8.6)
    assert "max-age" not in cookie and "expires" not in cookie


def test_login_rejects_wrong_password(guarded_client, monkeypatch):
    monkeypatch.setattr(api.auth, "is_configured", lambda: True)
    monkeypatch.setattr(api.auth, "verify_password", lambda p: False)
    assert guarded_client.post("/api/login", json={"password": "nope"}).status_code == 401


def test_login_locks_out_after_repeated_failures(guarded_client, monkeypatch):
    """연속 실패가 임계를 넘으면 맞는 비밀번호도 받지 않는다(8.6 무차별 대입 차단)."""
    monkeypatch.setattr(api.auth, "is_configured", lambda: True)
    monkeypatch.setattr(api.auth, "verify_password", lambda p: p == "ok")
    monkeypatch.setattr(api.auth, "issue_token", lambda: "TOKEN")
    for _ in range(api.auth.LOCKOUT_THRESHOLD):
        assert guarded_client.post("/api/login", json={"password": "no"}).status_code == 401
    r = guarded_client.post("/api/login", json={"password": "ok"})
    assert r.status_code == 429


def test_successful_login_clears_the_counter(guarded_client, monkeypatch):
    monkeypatch.setattr(api.auth, "is_configured", lambda: True)
    monkeypatch.setattr(api.auth, "verify_password", lambda p: p == "ok")
    monkeypatch.setattr(api.auth, "issue_token", lambda: "TOKEN")
    for _ in range(api.auth.LOCKOUT_THRESHOLD - 1):
        guarded_client.post("/api/login", json={"password": "no"})
    assert guarded_client.post("/api/login", json={"password": "ok"}).status_code == 200
    # 세었던 실패가 지워졌으므로 다시 임계까지 여유가 생긴다
    for _ in range(api.auth.LOCKOUT_THRESHOLD - 1):
        assert guarded_client.post("/api/login", json={"password": "no"}).status_code == 401


# ── ① 나의 정보 ──────────────────────────────────────────────────
def test_account_empty_is_not_an_error(client):
    body = client.get("/api/account").json()
    assert body["snapshot"] is None and body["holdings"] == []
    assert body["cumulative_net_flow"] == 0.0
    assert body["twr_return"] is None and body["safe_withdrawable"] is None


def test_account_returns_snapshot_and_holdings(conn, client):
    _symbol(conn, "005930", "삼성전자")
    _cycle(conn, "C1")
    journal.record_account_snapshot(
        conn, cycle_id="C1", cash=8_000_000, position_value=2_000_000,
        total_asset=10_000_000, base_asset=10_500_000, trade_date=_DAY,
    )
    journal.upsert_entry_position(
        conn, cycle_id="C1", symbol_id="005930", add_quantity=20, fill_price=100_000.0,
        entry_decision_id=None, current_stop_price=94_000.0,
        initial_stop_price=94_000.0, entry_date=_DAY,
    )
    journal.record_cycle_scores(conn, "C1", [
        {"symbol_id": "005930", "inclusion": "holding", "last_price": 110_000.0,
         "total_score": 0.71},
    ])

    body = client.get("/api/account").json()
    snap = body["snapshot"]
    assert snap["amount"] == 8_000_000 and snap["total_asset"] == 10_000_000
    assert snap["day_return_percent"] == pytest.approx(10_000_000 / 10_500_000 - 1)

    h = body["holdings"][0]
    assert h["name"] == "삼성전자" and h["quantity"] == 20
    assert h["last_price"] == 110_000                     # CycleScores에서 온 값
    assert h["profit_loss"] == pytest.approx(200_000)     # (110000-100000)×20
    assert h["return_percent"] == pytest.approx(0.10)
    assert h["current_stop_price"] == 94_000


def test_holding_days_counted_in_trading_days(conn, client, monkeypatch):
    _symbol(conn, "005930", "삼성전자")
    _cycle(conn, "C1")
    journal.upsert_entry_position(
        conn, cycle_id="C1", symbol_id="005930", add_quantity=1, fill_price=100.0,
        entry_decision_id=None, current_stop_price=90.0, initial_stop_price=90.0,
        entry_date=date(2026, 8, 3),
    )
    monkeypatch.setattr(api, "kst_today", lambda: date(2026, 8, 10))
    h = client.get("/api/account").json()["holdings"][0]
    assert h["holding_days"] == 5                          # 달력 7일 ≠ 거래일 5일


def test_missing_price_does_not_break_account(conn, client):
    _symbol(conn, "005930", "삼성전자")
    _cycle(conn, "C1")
    journal.upsert_entry_position(
        conn, cycle_id="C1", symbol_id="005930", add_quantity=1, fill_price=100.0,
        entry_decision_id=None, current_stop_price=90.0, initial_stop_price=90.0,
    )
    h = client.get("/api/account").json()["holdings"][0]
    assert h["last_price"] is None and h["profit_loss"] is None


def test_closed_positions_excluded(conn, client):
    _symbol(conn, "005930", "삼성전자")
    _cycle(conn, "C1")
    pid = journal.upsert_entry_position(
        conn, cycle_id="C1", symbol_id="005930", add_quantity=1, fill_price=100.0,
        entry_decision_id=None, current_stop_price=90.0, initial_stop_price=90.0,
    )
    journal.close_position(conn, pid)
    assert client.get("/api/account").json()["holdings"] == []


# ── ② 수익 그래프 ────────────────────────────────────────────────
def _outcome(conn, oid: str, code: str, day: date, net: float, r: float = 1.0) -> None:
    journal.record_outcome(
        conn, outcome_id=oid, position_id=None, symbol_id=code,
        entry_price=100.0, exit_price=100.0 + net, quantity=1, holding_days=3,
        gross_profit_loss=net, net_profit_loss=net, return_percent=net / 100.0,
        exit_reason="trail", r_multiple=r, exit_date=day, entry_date=day,
        closed_at=datetime.combine(day, datetime.min.time(), tzinfo=KST),
    )


def test_equity_curve_accumulates_net_profit(conn, client):
    _symbol(conn, "A", "에이")
    _outcome(conn, "O1", "A", date(2026, 8, 3), 100.0)
    _outcome(conn, "O2", "A", date(2026, 8, 4), -40.0)
    _outcome(conn, "O3", "A", date(2026, 8, 5), 25.0)
    pts = client.get("/api/equity-curve").json()["points"]
    assert [p["cumulative"] for p in pts] == [100.0, 60.0, 85.0]
    assert pts[0]["name"] == "에이" and pts[0]["r_multiple"] == 1.0


def test_equity_curve_filters_by_date(conn, client):
    _symbol(conn, "A", "에이")
    _outcome(conn, "O1", "A", date(2026, 8, 3), 100.0)
    _outcome(conn, "O2", "A", date(2026, 8, 20), 50.0)
    pts = client.get("/api/equity-curve?start=2026-08-10").json()["points"]
    assert len(pts) == 1 and pts[0]["outcome_id"] == "O2"


def test_equity_curve_returns_fill_markers(conn, client):
    _symbol(conn, "A", "에이")
    _cycle(conn, "C1")
    ts = datetime(2026, 8, 28, 14, 30, tzinfo=KST)
    _order(conn, "c1-A-buy-0", code="A", side="buy", purpose="entry",
           ordered_at=ts, cycle_id="C1")
    _order(conn, "c1-A-exit-0", code="A", side="sell", purpose="exit",
           ordered_at=ts + timedelta(days=3), cycle_id="C1")
    _order(conn, "c1-A-stop-0", code="A", side="sell", purpose="stop",
           ordered_at=ts, cycle_id="C1", filled=0)
    markers = client.get("/api/equity-curve").json()["markers"]
    # 체결된 진입·청산만 점으로 찍는다 — 미체결 스톱 예약은 거래가 아니다
    assert {m["purpose"] for m in markers} == {"entry", "exit"}


def test_equity_curve_includes_index_benchmarks(conn, client):
    journal.upsert_market_index(conn, "KOSPI", [
        {"date": date(2026, 8, 3), "close": 3000.0, "regime": "uptrend"},
        {"date": date(2026, 8, 4), "close": 3030.0, "regime": "uptrend"},
    ])
    journal.upsert_market_index(conn, "KOSDAQ", [
        {"date": date(2026, 8, 3), "close": 900.0, "regime": "uptrend"},
    ])
    bench = client.get("/api/equity-curve").json()["benchmarks"]
    assert {b["index_code"] for b in bench} == {"KOSPI", "KOSDAQ"}
    assert len(bench) == 3


# ── ② 균등가중 워치리스트 벤치마크 ───────────────────────────────
def test_watchlist_benchmark_averages_next_day_return(conn, client):
    for code in ("A", "B"):
        _symbol(conn, code, code)
    d0, d1 = date(2026, 8, 3), date(2026, 8, 4)
    journal.upsert_daily_bars(conn, "A", [
        {"date": d0, "close": 100.0, "volume": 1}, {"date": d1, "close": 110.0, "volume": 1},
    ])
    journal.upsert_daily_bars(conn, "B", [
        {"date": d0, "close": 100.0, "volume": 1}, {"date": d1, "close": 90.0, "volume": 1},
    ])
    journal.upsert_daily_scores(conn, d0, [
        {"symbol_id": "A", "passed_filter": True, "rank": 1, "total_score": 0.9},
        {"symbol_id": "B", "passed_filter": True, "rank": 2, "total_score": 0.8},
    ])
    body = client.get("/api/benchmark/watchlist").json()
    # A +10%, B −10% → 균등가중 0%
    assert body["series"][0]["cumulative"] == pytest.approx(0.0)
    assert body["series"][0]["names"] == 2


def test_watchlist_benchmark_excludes_filtered_out(conn, client):
    for code in ("A", "B"):
        _symbol(conn, code, code)
    d0, d1 = date(2026, 8, 3), date(2026, 8, 4)
    for code, c1 in (("A", 110.0), ("B", 90.0)):
        journal.upsert_daily_bars(conn, code, [
            {"date": d0, "close": 100.0, "volume": 1}, {"date": d1, "close": c1, "volume": 1},
        ])
    journal.upsert_daily_scores(conn, d0, [
        {"symbol_id": "A", "passed_filter": True, "rank": 1},
        {"symbol_id": "B", "passed_filter": False, "filter_reason": "동전주"},
    ])
    body = client.get("/api/benchmark/watchlist").json()
    assert body["series"][0]["names"] == 1                      # 탈락 종목은 안 담는다
    assert body["series"][0]["cumulative"] == pytest.approx(0.10)


def test_watchlist_benchmark_empty_is_not_an_error(client):
    assert client.get("/api/benchmark/watchlist").json()["series"] == []


# ── ③ 거래 리포트 — KST 날짜 경계 ────────────────────────────────
def test_trades_date_filter_uses_kst_not_utc(conn, client):
    """UTC로 거르면 9시간이 밀려 다른 날 주문이 섞인다(07-model 공통 규칙)."""
    _symbol(conn, "A", "에이")
    _cycle(conn, "C1")
    # KST 2026-08-28 01:00 == UTC 2026-08-27 16:00
    _order(conn, "early", code="A", side="buy", purpose="entry", cycle_id="C1",
           ordered_at=datetime(2026, 8, 28, 1, 0, tzinfo=KST))
    # KST 2026-08-27 23:00 == UTC 2026-08-27 14:00
    _order(conn, "prev-day", code="A", side="buy", purpose="entry", cycle_id="C1",
           ordered_at=datetime(2026, 8, 27, 23, 0, tzinfo=KST))

    got = client.get("/api/trades?start=2026-08-28").json()["orders"]
    assert [o["client_order_id"] for o in got] == ["early"]        # 전날 것이 안 섞인다

    got = client.get("/api/trades?end=2026-08-27").json()["orders"]
    assert [o["client_order_id"] for o in got] == ["prev-day"]     # 다음날 것이 안 섞인다


def test_trades_end_is_inclusive_of_whole_kst_day(conn, client):
    _symbol(conn, "A", "에이")
    _cycle(conn, "C1")
    _order(conn, "late", code="A", side="buy", purpose="entry", cycle_id="C1",
           ordered_at=datetime(2026, 8, 28, 23, 59, tzinfo=KST))
    got = client.get("/api/trades?start=2026-08-28&end=2026-08-28").json()["orders"]
    assert len(got) == 1                       # 그날 23:59도 그날이다


def test_trades_excludes_stop_reservations_by_default(conn, client):
    _symbol(conn, "A", "에이")
    _cycle(conn, "C1")
    ts = datetime(2026, 8, 28, 14, 30, tzinfo=KST)
    _order(conn, "entry", code="A", side="buy", purpose="entry", cycle_id="C1", ordered_at=ts)
    _order(conn, "stop", code="A", side="sell", purpose="stop", cycle_id="C1",
           ordered_at=ts, filled=0)
    assert [o["client_order_id"] for o in
            client.get("/api/trades").json()["orders"]] == ["entry"]
    assert len(client.get("/api/trades?include_stops=true").json()["orders"]) == 2


def test_trades_side_filter(conn, client):
    _symbol(conn, "A", "에이")
    _cycle(conn, "C1")
    ts = datetime(2026, 8, 28, 14, 30, tzinfo=KST)
    _order(conn, "b", code="A", side="buy", purpose="entry", cycle_id="C1", ordered_at=ts)
    _order(conn, "s", code="A", side="sell", purpose="exit", cycle_id="C1", ordered_at=ts)
    assert [o["side"] for o in
            client.get("/api/trades?side=buy").json()["orders"]] == ["buy"]
    assert len(client.get("/api/trades?side=bogus").json()["orders"]) == 2


def test_trades_limit_is_capped(conn, client):
    _symbol(conn, "A", "에이")
    _cycle(conn, "C1")
    ts = now_utc()
    for i in range(5):
        _order(conn, f"o{i}", code="A", side="buy", purpose="entry", cycle_id="C1",
               ordered_at=ts + timedelta(seconds=i))
    assert len(client.get("/api/trades?limit=2").json()["orders"]) == 2
    assert len(client.get("/api/trades?limit=99999").json()["orders"]) == 5


# ── ③ 거래 상세 — 근거 펼침 ──────────────────────────────────────
def test_trade_detail_404_for_unknown(client):
    assert client.get("/api/trades/nope").status_code == 404


def test_trade_detail_joins_decision_scores_and_checks(conn, client):
    from core.schemas import OrderAction, ProposedOrder

    _symbol(conn, "A", "에이")
    _cycle(conn, "C1")
    journal.record_decisions(
        conn, "C1", [ProposedOrder(code="A", action=OrderAction.BUY, risk_budget=0.7)],
        entry_threshold=0.6, target_positions=20,
    )
    did = "C1_A_buy"
    journal.record_cycle_scores(conn, "C1", [
        {"symbol_id": "A", "inclusion": "topRank", "total_score": 0.7,
         "last_price": 1000.0, "atr": 30.0, "stop_width": 60.0, "is_tradable": True},
    ])
    journal.record_risk_check(conn, cycle_id="C1", check_order=4,
                              check_name="circuitBreaker", result="pass")
    journal.record_risk_check(conn, cycle_id="C1", check_order=7,
                              check_name="symbolState", result="pass", decision_id=did)
    _order(conn, "c1-A-buy-0", code="A", side="buy", purpose="entry", cycle_id="C1",
           decision_id=did, ordered_at=now_utc())

    body = client.get("/api/trades/c1-A-buy-0").json()
    assert body["decision"]["action"] == "buy" and body["decision"]["threshold"] == 0.6
    assert body["cycle_score"]["stop_width"] == 60.0
    # 사이클 단위 검사(DecisionId NULL)와 결정 단위 검사가 순서대로 함께 온다
    assert [c["check_order"] for c in body["risk_checks"]] == [4, 7]


def test_trade_detail_without_decision_is_not_an_error(conn, client):
    # 상주 스톱 자동 체결은 CycleId·DecisionId가 없다(07-model 7.3)
    _symbol(conn, "A", "에이")
    _order(conn, "orphan", code="A", side="sell", purpose="stop", ordered_at=now_utc())
    body = client.get("/api/trades/orphan").json()
    assert body["decision"] is None and body["risk_checks"] == []


# ── ④ 오류·정지 ─────────────────────────────────────────────────
def test_alerts_empty(client):
    body = client.get("/api/alerts").json()
    assert body["active_stop"] is False and body["safe_stops"] == []


def test_alerts_flags_unreleased_safe_stop(conn, client):
    _cycle(conn, "C1")
    journal.record_safe_stop(conn, cause="잔고 불일치", cycle_id="C1")
    body = client.get("/api/alerts").json()
    assert body["active_stop"] is True                    # 비어 있는 해제시각 = 지금 정지 중
    assert body["safe_stops"][0]["cause"] == "잔고 불일치"


def test_alerts_clears_after_release(conn, client):
    _cycle(conn, "C1")
    eid = journal.record_safe_stop(conn, cause="데이터 오류", cycle_id="C1")
    journal.release_safe_stop(conn, eid, released_by="owner", reason="원인 확인")
    assert client.get("/api/alerts").json()["active_stop"] is False


def test_alerts_lists_failed_cycles_and_ingests(conn, client):
    _cycle(conn, "C1")
    journal.advance_status(conn, "C1", "failed", failed_step=4, skip_reason="시세 이상")
    _cycle(conn, "C2")
    journal.advance_status(conn, "C2", "recorded")
    journal.record_ingest_run(
        conn, run_id="R1", target_table="daily_bars", source="kis", status="partial",
        started_at=now_utc(), range_end=_DAY, error_message="12종목 실패",
    )
    journal.record_ingest_run(
        conn, run_id="R2", target_table="daily_scores", source="journal", status="ok",
        started_at=now_utc(), range_end=_DAY,
    )
    body = client.get("/api/alerts").json()
    assert [c["cycle_id"] for c in body["failed_cycles"]] == ["C1"]
    assert body["failed_cycles"][0]["failed_step"] == 4
    assert [i["run_id"] for i in body["failed_ingests"]] == ["R1"]   # ok는 안 뜬다


# ── 외부 현금흐름 반영 (08-dashboard 8.4) ────────────────────────
def _flow(conn, cycle_id: str, *, kind: str, amount: float, status: str = "unconfirmed"):
    return journal.record_cash_flow(
        conn, cycle_id, kind=kind, amount=amount, source="residual",
        expected=8_000_000, actual=8_000_000 + amount, mode="paper",
        status=status, trade_date=_DAY,
    )


def test_account_reports_net_flow_and_safe_withdrawable(conn, client):
    """① 나의 정보 — 누적 순입금·TWR·안전 출금 가능액."""
    _symbol(conn, "005930", "삼성전자")
    _cycle(conn, "C1")
    journal.record_account_snapshot(
        conn, cycle_id="C1", cash=8_000_000, position_value=2_000_000,
        total_asset=10_000_000, base_asset=8_000_000,
        net_flow_since_base=2_000_000, flow_this_snapshot=2_000_000, trade_date=_DAY,
    )
    body = client.get("/api/account").json()
    assert body["cumulative_net_flow"] == 2_000_000
    assert body["twr_return"] == pytest.approx(0.0)
    # 미체결 매수가 없으면 예수금 전액이 안전 출금 가능액
    assert body["safe_withdrawable"] == 8_000_000


def test_safe_withdrawable_subtracts_pending_buys(conn, client):
    """미체결 매수는 이미 쓰기로 정해진 돈이라 먼저 뺀다 — 미수 방지의 실질적 수단."""
    _cycle(conn, "C1")
    journal.record_account_snapshot(
        conn, cycle_id="C1", cash=8_000_000, position_value=0,
        total_asset=8_000_000, trade_date=_DAY,
    )
    journal.record_order(
        conn, client_order_id="o1", cycle_id="C1", decision_id=None,
        symbol_id="005930", side="buy", purpose="entry", order_type="00",
        order_quantity=10, filled_quantity=0, order_price=300_000,
        status="submitted", mode="paper", ordered_at=now_utc(),
    )
    assert client.get("/api/account").json()["safe_withdrawable"] == 8_000_000 - 3_000_000


def test_equity_curve_axis_totalasset_shows_flow_markers(conn, client):
    """② 수익 그래프 — 총자산 축에는 입출금 마커가 붙는다."""
    _cycle(conn, "C1")
    journal.record_account_snapshot(
        conn, cycle_id="C1", cash=12_000_000, position_value=0,
        total_asset=12_000_000, trade_date=_DAY,
    )
    _flow(conn, "C1", kind="deposit", amount=2_000_000)
    body = client.get("/api/equity-curve?axis=totalAsset").json()
    assert body["axis"] == "totalAsset"
    assert body["points"][0]["cumulative"] == 12_000_000
    assert len(body["flow_markers"]) == 1
    assert body["flow_markers"][0]["direction"] == "deposit"


def test_realized_axis_never_shows_flow_markers(conn, client):
    """실현손익 축은 이체와 무관하다 — 마커를 찍으면 없는 인과를 암시한다."""
    _cycle(conn, "C1")
    _flow(conn, "C1", kind="deposit", amount=2_000_000)
    body = client.get("/api/equity-curve").json()
    assert body["axis"] == "realized" and body["flow_markers"] == []


def test_equity_curve_rejects_unknown_axis(client):
    assert client.get("/api/equity-curve?axis=nope").status_code == 400


def test_dividend_is_not_a_flow_marker(conn, client):
    """배당은 수익이라 이체 마커로 찍지 않는다(09-eval Kind별 회계 처리)."""
    _cycle(conn, "C1")
    journal.record_account_snapshot(
        conn, cycle_id="C1", cash=1_000, position_value=0,
        total_asset=1_000, trade_date=_DAY,
    )
    _flow(conn, "C1", kind="dividend", amount=30_000)
    assert client.get("/api/equity-curve?axis=twr").json()["flow_markers"] == []


def test_trades_include_flows_and_can_be_filtered(conn, client):
    """③ 거래 리포트 — 주문과 입출금을 한 줄로 섞고, side로 갈라 볼 수 있다."""
    _symbol(conn, "005930", "삼성전자")
    _cycle(conn, "C1")
    _order(conn, "o1", code="005930", side="buy", purpose="entry",
           ordered_at=now_utc(), cycle_id="C1")
    _flow(conn, "C1", kind="withdrawal", amount=-500_000)

    body = client.get("/api/trades").json()
    assert len(body["orders"]) == 1 and len(body["flows"]) == 1
    assert float(body["flows"][0]["amount"]) == -500_000        # 출금은 음수

    assert client.get("/api/trades?side=flow").json()["orders"] == []
    assert client.get("/api/trades?side=buy").json()["flows"] == []


def test_alerts_list_unlabeled_flows_as_information(conn, client):
    """④ 오류·정지 — 미분류 흐름은 차단이 아니라 정보성 항목이다."""
    _cycle(conn, "C1")
    _flow(conn, "C1", kind="unknown", amount=2_000_000)
    body = client.get("/api/alerts").json()
    assert len(body["unlabeled_flows"]) == 1
    assert not body["active_stop"]                              # 매매를 막고 있지 않다
    assert "ops.cashflow" in body["unlabeled_flow_hint"]


def test_cash_flows_endpoint_is_read_only(conn, client, guarded_client):
    """라벨 붙이기는 CLI 몫 — 대시보드에 쓰기 경로가 없다(8.1 읽기 전용 경계)."""
    _cycle(conn, "C1")
    _flow(conn, "C1", kind="unknown", amount=1_000_000)
    assert len(client.get("/api/cash-flows?status_filter=unconfirmed").json()["flows"]) == 1
    assert guarded_client.post("/api/cash-flows").status_code in (401, 405)
