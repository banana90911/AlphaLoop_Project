"""
description:        읽기 전용 조회 API (FastAPI, 대시보드 전용)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/31
remarks:
"""

from datetime import date
from typing import Annotated, Any

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config.settings import get_settings, load_params
from core.timeutils import kst_day_bounds, kst_today
from core.trading_days import trading_days_between
from dashboard import auth
from memory import journal
from memory.db import connect

app = FastAPI(title="AlphaLoop Dashboard API", docs_url=None, redoc_url=None)

# 화면은 Vercel, API는 NCP다(08-dashboard 8.5). 브라우저는 다른 출처로 나가는 조회를
# 기본적으로 막으므로, 내 화면 주소만 명시적으로 연다. 목록이 비어 있으면 아무 데도
# 열지 않는다 — 주소를 아는 사람이 있어도 브라우저에서는 아무것도 못 읽는다.
_ORIGINS = [o.strip() for o in get_settings().dashboard_allowed_origins.split(",") if o.strip()]
if _ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ORIGINS,     # 와일드카드를 쓰지 않는다 — 쿠키를 함께 보내야 해서
        allow_credentials=True,     # 출입증 쿠키가 요청에 실리려면 필요하다
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

# 목록 조회 상한 — 화면이 한 번에 그릴 수 있는 양을 넘지 않게 서버가 막는다
MAX_LIMIT = 1000
BENCH_LIMIT = 2000          # 지수 시계열(코스피·코스닥 각각)


# ── 연결·인증 ────────────────────────────────────────────────────
def db():
    """요청 하나당 읽기 전용 연결을 만든다."""
    conn = connect(read_only=True)
    try:
        yield conn
    finally:
        conn.close()


def require_token(session: str | None = Cookie(default=None, alias=auth.COOKIE_NAME)):
    """출입증을 검증한다(통과 못 하면 401)."""
    if not auth.verify_token(session):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다")


Guarded = [Depends(require_token)]
# 인자 기본값에 Depends를 쓰는 대신 Annotated로 붙인다(ruff B008 회피 + FastAPI 권장).
DbConn = Annotated[Any, Depends(db)]


class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
def login(body: LoginBody, response: Response, request: Request) -> dict:
    """비밀번호로 출입증을 발급한다(성공·실패 모두 로그에 남김).

    연속 실패가 쌓이면 그 주소를 잠근다. 출입증 유효기간을 줄이는 것보다 이 문을
    잠그는 쪽이 실제 안전에 크게 기여한다 — 출입증은 화면 코드가 못 읽는 쿠키에 있지만
    로그인 문은 주소만 알면 누구나 두드릴 수 있기 때문이다(8.6).
    """
    if not auth.is_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "로그인이 설정되지 않았습니다")
    source = request.client.host if request.client else ""

    wait = auth.locked_seconds(source)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"시도가 너무 많습니다. {wait // 60 + 1}분 뒤에 다시 시도하세요",
        )

    ok = auth.verify_password(body.password)
    auth.log_attempt(ok, source)
    auth.record_attempt(ok, source)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "비밀번호가 다릅니다")
    response.set_cookie(value=auth.issue_token(), **auth.cookie_kwargs())
    return {"ok": True, "expires_hours": auth.TOKEN_TTL_HOURS}


@app.post("/api/logout")
def logout(response: Response) -> dict:
    """출입증 쿠키를 지운다."""
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@app.get("/api/health")
def health() -> dict:
    """서버 생존 확인(로그인 불필요, 데이터 없음)."""
    return {"ok": True}


# ── ① 나의 정보 ──────────────────────────────────────────────────
@app.get("/api/account", dependencies=Guarded)
def get_account(conn: DbConn) -> dict:
    """총자본·예수금·당일 손익률과 보유 종목(평가 기준 시각 포함)을 반환한다."""
    snap = conn.execute(
        'SELECT * FROM account_snapshots ORDER BY recorded_date_time DESC LIMIT 1'
    ).fetchone()
    rows = conn.execute(
        'SELECT p.position_id, p.symbol_id, s.name, p.quantity, p.average_price, '
        'p.current_stop_price, p.initial_stop_price, p.entry_date, p.status, '
        '  (SELECT c.last_price FROM cycle_scores c '
        '   WHERE c.symbol_id = p.symbol_id AND c.last_price IS NOT NULL '
        '   ORDER BY c.scored_date_time DESC LIMIT 1) AS last_price, '
        '  (SELECT c.scored_date_time FROM cycle_scores c '
        '   WHERE c.symbol_id = p.symbol_id AND c.last_price IS NOT NULL '
        '   ORDER BY c.scored_date_time DESC LIMIT 1) AS priced_at '
        'FROM positions p LEFT JOIN symbols s ON s.symbol_id = p.symbol_id '
        "WHERE p.status = 'open' ORDER BY p.entry_date",
    ).fetchall()
    today = kst_today()
    holdings = []
    for r in rows:
        last, avg = r["last_price"], r["average_price"]
        holdings.append({
            **{k: r[k] for k in ("position_id", "symbol_id", "name", "quantity",
                                 "average_price", "current_stop_price", "initial_stop_price",
                                 "entry_date", "last_price", "priced_at")},
            "profit_loss": (last - avg) * r["quantity"] if last is not None else None,
            "return_percent": (last / avg - 1) if last is not None and avg else None,
            # 보유일수는 거래일로 센다 — 청산 규칙과 같은 단위여야 화면과 규칙이 어긋나지 않는다
            "holding_days": (
                trading_days_between(r["entry_date"], today) if r["entry_date"] else None
            ),
        })
    return {
        "snapshot": dict(snap) if snap else None,
        "holdings": holdings,
        # 개시 이후 누적 순입금. `TotalAsset − 이 값 = 누적 순손익`으로 검산된다(07-model)
        "cumulative_net_flow": float(snap["cumulative_net_flow"]) if snap else 0.0,
        # TWR 지수를 수익률로 환산 — 이체가 섞여도 안 흔들리는 유일한 비율 지표(09-eval)
        "twr_return": (
            float(snap["twr_index"]) - 1.0
            if snap and snap["twr_index"] is not None else None
        ),
        # 이체 전에 폰에서 이 숫자를 보는 게 미수를 막는 실질적 유일한 수단(08-dashboard 8.4)
        "safe_withdrawable": _safe_withdrawable(conn, snap),
    }


def _safe_withdrawable(conn, snap) -> float | None:
    """안전 출금 가능액 = 예수금 − 미체결 매수 주문 금액.

    이만큼까지는 빼도 미수가 안 난다. 미체결 매수는 아직 돈이 안 나갔을 뿐 이미
    쓰기로 정해진 돈이라 예수금에서 먼저 빼고 봐야 한다.
    """
    if snap is None:
        return None
    row = conn.execute(
        'SELECT COALESCE(SUM((order_quantity - filled_quantity) * order_price), 0) AS v '
        'FROM orders WHERE side = \'buy\' AND order_price IS NOT NULL '
        "AND status IN ('submitted','partial')"
    ).fetchone()
    return max(0.0, float(snap["amount"]) - float(row["v"] or 0))


# ── ② 수익 그래프 ────────────────────────────────────────────────
# 자본 그래프의 세로축 3종(08-dashboard 8.4 ②).
#   realized   — 청산 실현손익 누적(원). 이체와 무관하므로 마커를 찍지 않는다.
#   totalAsset — 계좌 총자산(원). 이체가 그대로 보이므로 **여기에 입출금 마커**를 찍는다.
#   twr        — 시간가중수익률 지수. 이체 효과가 제거된 유일한 비율 축.
AXES = ("realized", "totalAsset", "twr")


@app.get("/api/equity-curve", dependencies=Guarded)
def get_equity_curve(conn: DbConn, start: date | None = None,
                     end: date | None = None, axis: str = "realized") -> dict:
    """자본 곡선을 축(axis)별로 반환한다 — 벤치마크·체결 시점·입출금 마커 포함.

    축을 셋으로 나눈 이유는 금액과 비율이 이체에 다르게 반응하기 때문이다. 총자산은
    입금하면 그냥 뛰지만 그건 수익이 아니고, 그 점프를 설명해 주는 것이 입출금 마커다.
    비율을 보고 싶으면 이체 효과가 제거된 `twr`를 봐야 한다(09-eval).
    """
    if axis not in AXES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"axis는 {', '.join(AXES)} 중 하나여야 합니다")
    points = (
        _realized_points(conn, start, end) if axis == "realized"
        else _snapshot_points(conn, start, end, axis)
    )
    return {
        "axis": axis,
        "points": points,
        "benchmarks": _benchmarks(conn, start, end),
        # 곡선 위에 찍을 매수·매도 시점 — 어느 구간의 상승이 어느 거래에서 나왔는지 잇는 용도
        "markers": _fill_markers(conn, start, end),
        # 실현손익 축은 이체와 무관하므로 마커를 안 찍는다(찍으면 없는 인과를 암시한다)
        "flow_markers": [] if axis == "realized" else _flow_markers(conn, start, end),
    }


def _realized_points(conn, start: date | None, end: date | None) -> list[dict]:
    """청산일 순 누적 실현손익(원). 이체가 절대 섞이지 않는 축이다."""
    sql = (
        'SELECT o.outcome_id, o.symbol_id, s.name, o.exit_date, o.net_profit_loss, '
        'o.r_multiple, o.exit_reason, o.return_percent '
        'FROM outcomes o LEFT JOIN symbols s ON s.symbol_id = o.symbol_id WHERE 1=1'
    )
    params: list[Any] = []
    if start:
        sql += ' AND o.exit_date >= %s'
        params.append(start)
    if end:
        sql += ' AND o.exit_date <= %s'
        params.append(end)
    rows = conn.execute(sql + ' ORDER BY o.exit_date, o.closed_date_time', params).fetchall()

    cum, points = 0.0, []
    for r in rows:
        cum += float(r["net_profit_loss"] or 0)
        points.append({**dict(r), "cumulative": cum})
    return points


def _snapshot_points(conn, start: date | None, end: date | None, axis: str) -> list[dict]:
    """스냅샷 시계열에서 총자산 또는 TWR 지수를 뽑는다."""
    sql = (
        'SELECT trade_date, total_asset, cumulative_net_flow, twr_index, '
        'day_return_percent, recorded_date_time FROM account_snapshots WHERE 1=1'
    )
    params: list[Any] = []
    if start:
        sql += ' AND trade_date >= %s'
        params.append(start)
    if end:
        sql += ' AND trade_date <= %s'
        params.append(end)
    params.append(BENCH_LIMIT)
    rows = conn.execute(sql + ' ORDER BY recorded_date_time LIMIT %s', params).fetchall()
    key = "total_asset" if axis == "totalAsset" else "twr_index"
    return [
        {**dict(r), "cumulative": float(r[key]) if r[key] is not None else None}
        for r in rows
    ]


def _flow_markers(conn, start: date | None, end: date | None) -> list[dict]:
    """곡선 위에 찍을 입출금 시점. 매수·매도 점과 구분되게 화면에서 회색 삼각형으로 그린다."""
    marks = ", ".join(["%s"] * len(journal.EXTERNAL_KINDS))
    sql = (
        f'SELECT flow_id, trade_date, kind, amount, status, source, '
        f'expected_cash, actual_cash, detected_date_time FROM cash_flows '
        f'WHERE kind IN ({marks})'
    )
    params: list[Any] = list(journal.EXTERNAL_KINDS)
    if start:
        sql += ' AND trade_date >= %s'
        params.append(start)
    if end:
        sql += ' AND trade_date <= %s'
        params.append(end)
    params.append(MAX_LIMIT)
    rows = conn.execute(sql + ' ORDER BY detected_date_time LIMIT %s', params).fetchall()
    return [
        {**dict(r), "direction": "deposit" if float(r["amount"]) >= 0 else "withdrawal"}
        for r in rows
    ]


def _benchmarks(conn, start: date | None, end: date | None) -> list[dict]:
    """코스피·코스닥 지수 시계열을 반환한다(기간으로 좁힘)."""
    sql = 'SELECT index_code, trade_date, close FROM market_indices WHERE 1=1'
    params: list[Any] = []
    if start:
        sql += ' AND trade_date >= %s'
        params.append(start)
    if end:
        sql += ' AND trade_date <= %s'
        params.append(end)
    params.append(BENCH_LIMIT)
    rows = conn.execute(sql + ' ORDER BY trade_date LIMIT %s', params).fetchall()
    return [dict(r) for r in rows]


def _fill_markers(conn, start: date | None, end: date | None) -> list[dict]:
    """체결된 진입·청산 시점(매수 초록·매도 빨강으로 찍을 점)을 반환한다."""
    sql = (
        'SELECT client_order_id, symbol_id, side, purpose, filled_quantity, '
        'average_fill_price, filled_date_time FROM orders '
        "WHERE filled_quantity > 0 AND purpose IN ('entry','exit')"
    )
    params: list[Any] = []
    if start:
        sql += ' AND filled_date_time >= %s'
        params.append(kst_day_bounds(start)[0])
    if end:
        sql += ' AND filled_date_time < %s'
        params.append(kst_day_bounds(end)[1])
    params.append(MAX_LIMIT)
    rows = conn.execute(sql + ' ORDER BY filled_date_time LIMIT %s', params).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/benchmark/watchlist", dependencies=Guarded)
def get_watchlist_benchmark(conn: DbConn, start: date | None = None,
                            end: date | None = None) -> dict:
    """균등가중 워치리스트 벤치마크 — 매일 점수 상위 N을 균등 보유했을 때의 누적수익.

    설계 8.4 ②의 세 번째 비교선이다. 전략이 "종목 고르기"로 버는지, 아니면 그날의
    상위권을 아무렇게나 담아도 나왔을 결과인지를 가른다.
    """
    top_n = int(load_params("risk_params").get("screener", {}).get("top_n", 40))
    sql = (
        'WITH picks AS ('
        '  SELECT trade_date, symbol_id FROM daily_scores '
        '  WHERE passed_filter AND rank IS NOT NULL AND rank <= %s'
        ')'
        ' SELECT p.trade_date, '
        '        avg(nb.close / b.close - 1.0) AS day_return, '
        '        count(*) AS names '
        ' FROM picks p '
        ' JOIN daily_bars b ON b.symbol_id = p.symbol_id AND b.trade_date = p.trade_date '
        ' JOIN LATERAL ('
        '   SELECT close FROM daily_bars x '
        '   WHERE x.symbol_id = p.symbol_id AND x.trade_date > p.trade_date '
        '   ORDER BY x.trade_date LIMIT 1'
        ' ) nb ON true '
        ' WHERE b.close > 0'
    )
    params: list[Any] = [top_n]
    if start:
        sql += ' AND p.trade_date >= %s'
        params.append(start)
    if end:
        sql += ' AND p.trade_date <= %s'
        params.append(end)
    rows = conn.execute(sql + ' GROUP BY p.trade_date ORDER BY p.trade_date', params).fetchall()

    cum, series = 1.0, []
    for r in rows:
        cum *= 1.0 + float(r["day_return"] or 0.0)
        series.append({"trade_date": r["trade_date"], "names": r["names"],
                       "cumulative": cum - 1.0})
    return {"top_n": top_n, "series": series}


# ── ③ 거래 리포트 ────────────────────────────────────────────────
@app.get("/api/trades", dependencies=Guarded)
def get_trades(conn: DbConn, start: date | None = None, end: date | None = None,
               side: str | None = None, include_stops: bool = False,
               limit: int = 200) -> dict:
    """주문과 입출금을 시간순으로 조회한다(KST 날짜로 좁힘).

    `side`: `buy`·`sell`은 주문만, `flow`는 입출금만, 생략하면 둘 다.
    입출금을 같이 내보내는 이유는 "이 구간에 왜 돈이 늘었나"를 한 화면에서
    설명하기 위해서다 — 매매와 이체가 따로 놀면 그 인과를 사람이 못 잇는다.
    """
    capped = max(1, min(limit, MAX_LIMIT))
    # 목록이 요구하는 열 중 둘은 "orders"에 없다(08-dashboard 8.4 ③) —
    #   손절가: 그 주문을 낳은 결정의 "stop_price"
    #   상태(보유/청산): 그 진입이 만든 포지션이 아직 열려 있는지
    # 화면에서 종목코드로 짐작하면 같은 종목을 재진입했을 때 옛 매수가 "보유"로 보인다.
    sql = (
        'SELECT o.*, s.name, d.stop_price, p.status AS position_status '
        'FROM orders o '
        'LEFT JOIN symbols s ON s.symbol_id = o.symbol_id '
        'LEFT JOIN decisions d ON d.decision_id = o.decision_id '
        'LEFT JOIN positions p ON p.entry_decision_id = o.decision_id WHERE 1=1'
    )
    params: list[Any] = []
    # OrderedDateTime은 timestamptz(UTC)다. KST 날짜를 UTC 구간으로 바꿔 거른다
    if start:
        sql += ' AND o.ordered_date_time >= %s'
        params.append(kst_day_bounds(start)[0])
    if end:
        sql += ' AND o.ordered_date_time < %s'
        params.append(kst_day_bounds(end)[1])
    if side in ("buy", "sell"):
        sql += ' AND o.side = %s'
        params.append(side)
    if not include_stops:
        # 손절 예약(stop)은 걸어둔 것이지 오간 거래가 아니다 — 기본은 뺀다
        sql += " AND o.purpose <> 'stop'"
    sql += ' ORDER BY o.ordered_date_time DESC LIMIT %s'
    params.append(capped)

    orders = [] if side == "flow" else [
        dict(r) for r in conn.execute(sql, params).fetchall()
    ]
    flows = [] if side in ("buy", "sell") else get_cash_flows(
        conn, start=start, end=end, limit=capped
    )["flows"]
    return {"orders": orders, "flows": flows}


@app.get("/api/cash-flows", dependencies=Guarded)
def get_cash_flows(conn: DbConn, start: date | None = None, end: date | None = None,
                   status_filter: str | None = None, limit: int = 200) -> dict:
    """감지된 외부 현금흐름(입출금·배당)을 시간순으로 반환한다.

    거래 리포트에서 주문과 한 줄로 섞어 보여주기 위한 것이다. 부호 규칙은 주문과
    같다 — 거래대금이 곧 현금 방향이므로 **입금 +, 출금 −**.
    라벨을 붙이는 것은 쓰기라서 여기 없다. `python -m ops.cashflow`가 그 일을 한다
    (08-dashboard 8.1 읽기 전용 경계).
    """
    sql = 'SELECT * FROM cash_flows WHERE 1=1'
    params: list[Any] = []
    if start:
        sql += ' AND trade_date >= %s'
        params.append(start)
    if end:
        sql += ' AND trade_date <= %s'
        params.append(end)
    if status_filter:
        sql += ' AND status = %s'
        params.append(status_filter)
    params.append(max(1, min(limit, MAX_LIMIT)))
    rows = conn.execute(sql + ' ORDER BY detected_date_time DESC LIMIT %s', params).fetchall()
    return {"flows": [dict(r) for r in rows]}


@app.get("/api/trades/{client_order_id}", dependencies=Guarded)
def get_trade_detail(client_order_id: str, conn: DbConn) -> dict:
    """한 거래의 진입 근거·게이트 결과·청산 결과를 모아 반환한다."""
    order = conn.execute(
        'SELECT * FROM orders WHERE client_order_id = %s', (client_order_id,)
    ).fetchone()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "주문을 찾을 수 없습니다")
    did, cid, sym = order["decision_id"], order["cycle_id"], order["symbol_id"]
    decision = conn.execute(
        'SELECT * FROM decisions WHERE decision_id = %s', (did,)
    ).fetchone() if did else None
    scores = conn.execute(
        'SELECT * FROM cycle_scores WHERE cycle_id = %s AND symbol_id = %s',
        (cid, sym),
    ).fetchone() if cid else None
    # 게이트 판정은 결정 단위(decision_id)와 사이클 단위(NULL) 둘 다 보여준다
    checks = conn.execute(
        'SELECT * FROM risk_checks WHERE cycle_id = %s '
        '  AND (decision_id = %s OR decision_id IS NULL) ORDER BY check_order',
        (cid, did),
    ).fetchall() if cid else []
    outcome = conn.execute(
        'SELECT * FROM outcomes WHERE entry_decision_id = %s '
        'OR exit_decision_id = %s ORDER BY closed_date_time DESC LIMIT 1', (did, did)
    ).fetchone() if did else None
    return {
        "order": dict(order),
        "decision": dict(decision) if decision else None,
        "cycle_score": dict(scores) if scores else None,
        "risk_checks": [dict(c) for c in checks],
        "outcome": dict(outcome) if outcome else None,
    }


# ── ④ 오류·정지 ─────────────────────────────────────────────────
@app.get("/api/alerts", dependencies=Guarded)
def get_alerts(conn: DbConn) -> dict:
    """정지·실패 사이클·배치 결과와 미분류 현금 변동을 반환한다(해제는 사람이 직접 개입).

    미분류 현금 변동은 **정보성 항목**이다 — 매매를 막고 있는 게 아니라 라벨이 아직
    안 붙었다는 안내일 뿐이다. 여기 뜨는 진짜 차단은 미수·대형 유출 SafeStop 둘뿐이다.
    """
    safe_stops = conn.execute(
        'SELECT * FROM safe_stop_events ORDER BY occurred_date_time DESC LIMIT 50'
    ).fetchall()
    cycles = conn.execute(
        'SELECT cycle_id, trade_date, status, failed_step, skip_reason, '
        'started_date_time FROM cycles '
        "WHERE status IN ('failed','skipped') "
        'ORDER BY started_date_time DESC LIMIT 50'
    ).fetchall()
    ingests = conn.execute(
        'SELECT * FROM ingest_runs WHERE status <> \'ok\' '
        'ORDER BY started_date_time DESC LIMIT 50'
    ).fetchall()
    unlabeled = conn.execute(
        'SELECT * FROM cash_flows WHERE status = \'unconfirmed\' '
        'ORDER BY detected_date_time DESC LIMIT 50'
    ).fetchall()
    return {
        "safe_stops": [dict(r) for r in safe_stops],
        # 비어 있는 ReleasedDateTime이 곧 "지금 정지 중"이다(08-dashboard 8.4 ④)
        "active_stop": any(r["released_date_time"] is None for r in safe_stops),
        "failed_cycles": [dict(r) for r in cycles],
        "failed_ingests": [dict(r) for r in ingests],
        # 대시보드는 읽기 전용이라 라벨을 못 붙인다 — 붙이는 방법만 알려준다
        "unlabeled_flows": [dict(r) for r in unlabeled],
        "unlabeled_flow_hint": "python -m ops.cashflow confirm --id <FlowId> --kind deposit",
    }
