"""
description:        대시보드 화면 개발용 데모 서버 (1인용 PG + 가짜 데이터 + uvicorn)
author:             siheon jung
created date:       2026/09/04
last modified date: 2026/09/04
remarks:            **개발 전용.** 운영 경로가 아니다 — 매매 코어도, 실제 DB도 건드리지 않는다.
                    pgserver로 임시 서버를 띄우고 임시 스키마 하나에 그럴듯한 기록을 채운 뒤
                    dashboard/api.py를 그 위에 올린다. 화면을 고칠 때마다 실계좌를 열지
                    않아도 되게 하는 것이 목적이다.

                    실행:  python -m dashboard.devserve
                    화면:  cd dashboard/web && npm run dev   →  http://localhost:5173
                    비밀번호는 실행할 때 터미널에 찍힌다(기본 "dev").
"""

from __future__ import annotations

import argparse
import os
import random
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

KST = "+09:00"
DEV_PASSWORD = "dev"
API_PORT = 8787

# 데모용 종목. 실제 코드·이름이지만 여기 들어가는 가격·수량은 전부 지어낸 값이다.
SYMBOLS = [
    ("005930", "삼성전자", "KOSPI", 74_000),
    ("000660", "SK하이닉스", "KOSPI", 231_000),
    ("373220", "LG에너지솔루션", "KOSPI", 385_000),
    ("207940", "삼성바이오로직스", "KOSPI", 812_000),
    ("035420", "NAVER", "KOSPI", 187_000),
    ("035720", "카카오", "KOSPI", 41_500),
    ("012450", "한화에어로스페이스", "KOSPI", 298_000),
    ("042700", "한미반도체", "KOSPI", 96_400),
    ("086520", "에코프로", "KOSDAQ", 78_300),
    ("247540", "에코프로비엠", "KOSDAQ", 161_000),
    ("196170", "알테오젠", "KOSDAQ", 322_000),
    ("058470", "리노공업", "KOSDAQ", 178_000),
]

DAYS = 260          # 약 1년치 거래일
START_CAPITAL = 1_000_000


def trading_days(n: int) -> list[date]:
    """오늘부터 거꾸로 n개의 평일을 만든다(휴장일은 신경 쓰지 않는 데모용)."""
    out: list[date] = []
    d = date.today()
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def many(conn, sql: str, rows: list) -> None:
    """psycopg3의 executemany는 커서에만 있다 — 매번 with를 쓰지 않으려고 감싼다."""
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def ts(day: date, hour: int, minute: int = 0) -> str:
    """KST 벽시계 시각을 timestamptz 리터럴로."""
    return f"{day.isoformat()} {hour:02d}:{minute:02d}:00{KST}"


def seed(conn) -> None:
    """빈 스키마에 한 해치 운영 기록을 채운다."""
    rng = random.Random(20260904)
    days = trading_days(DAYS)
    now = datetime.now()

    # ── 종목 명부 ────────────────────────────────────────────
    many(
        conn,
        'INSERT INTO symbols (symbol_id,name,market,security_type,'
        'listed_date,last_update_date_time) VALUES (%s,%s,%s,\'common\',%s,%s)',
        [(sid, name, mkt, date(2010, 1, 4), now) for sid, name, mkt, _ in SYMBOLS],
    )

    # ── 지수·일봉 ────────────────────────────────────────────
    # 코스피는 완만히, 코스닥은 더 출렁이게. 우리 곡선이 이 둘을 이기는 그림을 만든다.
    idx = {"KOSPI": 2_580.0, "KOSDAQ": 745.0}
    drift = {"KOSPI": 0.0006, "KOSDAQ": 0.0004}
    vol = {"KOSPI": 0.0082, "KOSDAQ": 0.0135}
    index_rows = []
    for day in days:
        for code in idx:
            idx[code] *= 1 + rng.gauss(drift[code], vol[code])
            index_rows.append((code, day, round(idx[code], 2), None,
                               "uptrend" if drift[code] > 0 else "downtrend", now))
    many(
        conn,
        'INSERT INTO market_indices (index_code,trade_date,close,sma_200,'
        'regime,collected_date_time) VALUES (%s,%s,%s,%s,%s,%s)',
        index_rows,
    )

    prices: dict[str, dict[date, float]] = {}
    bar_rows, score_rows = [], []
    for sid, _, _, base in SYMBOLS:
        px = base * rng.uniform(0.55, 0.8)      # 1년 전 가격에서 출발해 오늘 base 근처로
        prices[sid] = {}
        for day in days:
            px *= 1 + rng.gauss(0.0011, 0.021)
            prices[sid][day] = px
            bar_rows.append((sid, day, round(px * 0.995, 0), round(px * 1.015, 0),
                             round(px * 0.985, 0), round(px, 0),
                             rng.randint(200_000, 4_000_000),
                             round(px * rng.randint(200_000, 4_000_000), 0)))
    many(
        conn,
        'INSERT INTO daily_bars (symbol_id,trade_date,open,high,low,close,'
        'volume,value) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
        bar_rows,
    )

    # 점수·순위 — ②의 균등가중 워치리스트 벤치마크가 이 표를 읽는다
    for day in days:
        ranked = sorted(SYMBOLS, key=lambda s: rng.random())
        for rank, (sid, _, _, _) in enumerate(ranked, start=1):
            total = max(0.05, min(0.98, rng.gauss(0.62, 0.16)))
            score_rows.append((day, sid, True, rng.gauss(0.12, 0.2),
                               rng.uniform(0.1, 0.95), rng.uniform(0.1, 0.95),
                               rng.uniform(0.1, 0.95), rng.uniform(0.1, 0.95),
                               total, rank, now))
    many(
        conn,
        'INSERT INTO daily_scores (trade_date,symbol_id,passed_filter,momentum,'
        'momentum_percentile,flow_percentile,value_percentile,low_volatility_percentile,'
        'total_score,rank,computed_date_time) '
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        score_rows,
    )

    # ── 사이클 ──────────────────────────────────────────────
    # 정기 사이클은 하루 한 번 14:30(project 확정 사항)
    cycle_ids = {day: f"C{day.strftime('%Y%m%d')}1430" for day in days}
    cycle_rows = [
        (cid, day, "recorded", None, None, "paper", ts(day, 14, 30), ts(day, 14, 32))
        for day, cid in cycle_ids.items()
    ]
    # 실패·건너뜀도 몇 건 섞는다 — ④가 비어 있으면 그 영역의 디자인을 볼 수 없다
    broken = days[-9]
    cycle_rows.append((f"C{broken.strftime('%Y%m%d')}1430F", broken, "failed", None, 5,
                       "paper", ts(broken, 14, 30), ts(broken, 14, 31)))
    skipped = days[-4]
    cycle_rows.append((f"C{skipped.strftime('%Y%m%d')}1430S", skipped, "skipped",
                       "marketHalt", None, "paper", ts(skipped, 14, 30), ts(skipped, 14, 30)))
    many(
        conn,
        'INSERT INTO cycles (cycle_id,trade_date,status,skip_reason,failed_step,'
        'mode,started_date_time,finished_date_time) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
        cycle_rows,
    )

    # ── 매매 시나리오 ────────────────────────────────────────
    # 20종목 동일가중이지만 데모 종목이 12개라 그 안에서 돌린다.
    decisions, orders, positions, outcomes, checks = [], [], [], [], []
    cscores: dict[tuple[str, str], tuple] = {}   # (CycleId, SymbolId)가 기본키
    open_positions: dict[str, dict] = {}
    realized_by_day: dict[date, float] = {}
    seq = 0

    for i, day in enumerate(days):
        cid = cycle_ids[day]

        # 청산 — 보유 5거래일 이상이면 확률적으로 판다
        for sid in list(open_positions):
            pos = open_positions[sid]
            held = i - pos["entry_i"]
            px = prices[sid][day]
            hit_stop = px <= pos["stop"]
            if not hit_stop and (held < 4 or rng.random() > 0.16):
                continue
            seq += 1
            qty = pos["qty"]
            entry = pos["avg"]
            exit_px = round(pos["stop"] if hit_stop else px, 0)
            gross = (exit_px - entry) * qty
            fee = round(abs(exit_px * qty) * 0.00015 + abs(entry * qty) * 0.00015, 0)
            tax = round(exit_px * qty * 0.0018, 0)
            net = gross - fee - tax
            reason = "stopHit" if hit_stop else rng.choice(["trail", "timeExit", "thesisInvalid"])
            did = f"D{cid}-{sid}-X"
            decisions.append((did, cid, sid, "exitAll", reason, None, None, exit_px,
                              pos["stop"], pos["risk"], None, qty, None, None, None,
                              "uptrend", ts(day, 14, 30)))
            oid = f"{cid}-{sid}-sell-{seq}"
            orders.append((oid, cid, did, f"K{seq:08d}", sid, "sell", "exit", "00", qty,
                           exit_px, None, qty, exit_px, fee, tax, "filled",
                           ts(day, 14, 30), ts(day, 14, 31), "paper"))
            r_multiple = net / (pos["risk"] * qty) if pos["risk"] else None
            outcomes.append((f"O{oid}", pos["pid"], pos["did"], did, sid, entry, exit_px,
                             qty, pos["entry_date"], day, held, gross, fee, tax, net,
                             exit_px / entry - 1, r_multiple, "full", reason,
                             pos["score"], 3, "uptrend", ts(day, 14, 31), "paper"))
            positions.append((pos["pid"], sid, pos["market"], qty, entry, pos["did"],
                              pos["entry_date"], pos["init_stop"], pos["stop"], pos["risk"],
                              True, None, "closed", ts(day, 14, 31), ts(day, 14, 31)))
            cscores[(cid, sid)] = (
                cid, sid, "holding", round(pos["score"] - rng.uniform(0.0, 0.1), 4),
                rng.uniform(0.1, 0.7), round(rng.uniform(0.2, 0.55), 4), px,
                rng.randint(100, 9000), rng.randint(100, 9000),
                round(pos["risk"] / 2, 0), pos["risk"], True, None, ts(day, 14, 30))
            realized_by_day[day] = realized_by_day.get(day, 0.0) + net
            del open_positions[sid]

        # 진입 — 최대 6종목까지 채운다
        if i > 3:
            candidates = [s for s in SYMBOLS if s[0] not in open_positions]
            rng.shuffle(candidates)
            for sid, _, mkt, _ in candidates:
                if len(open_positions) >= 6 or rng.random() > 0.28:
                    continue
                seq += 1
                px = round(prices[sid][day], 0)
                atr = round(px * rng.uniform(0.022, 0.038), 0)
                stop = round(px - 2 * atr, 0)
                risk = px - stop
                qty = max(1, int(START_CAPITAL * 2.2 / 6 / px))
                score = round(rng.uniform(0.63, 0.92), 4)
                did = f"D{cid}-{sid}-E"
                decisions.append((did, cid, sid, "buy", "entryThreshold", score, 0.62, px,
                                  stop, risk, 6, qty, round(rng.uniform(1.4, 3.1), 2),
                                  round(px * qty * 0.0033, 0), round(px * qty * 0.012, 0),
                                  "uptrend", ts(day, 14, 30)))
                oid = f"{cid}-{sid}-buy-{seq}"
                orders.append((oid, cid, did, f"K{seq:08d}", sid, "buy", "entry", "00", qty,
                               px, None, qty, px, round(px * qty * 0.00015, 0), None,
                               "filled", ts(day, 14, 30), ts(day, 14, 31), "paper"))
                pid = f"P{oid}"
                open_positions[sid] = {
                    "pid": pid, "did": did, "qty": qty, "avg": px, "stop": stop,
                    "init_stop": stop, "risk": risk, "entry_i": i, "entry_date": day,
                    "market": mkt, "score": score,
                }
                cscores[(cid, sid)] = (
                    cid, sid, "topRank", round(score - rng.uniform(0.0, 0.06), 4),
                    rng.uniform(0.4, 0.98), score, px, rng.randint(100, 9000),
                    rng.randint(100, 9000), atr, 2 * atr, True, None, ts(day, 14, 30))
                at = ts(day, 14, 30)
                checks += [
                    (f"{did}-1", cid, None, 1, "balanceSync", "pass", None, None, None, at),
                    (f"{did}-4", cid, None, 4, "dataFreshness", "pass", None, None, None, at),
                    (f"{did}-6", cid, did, 6, "hardLimit", "pass", "종목당 한도 이내",
                     25.0, round(px * qty / (START_CAPITAL * 2.2) * 100, 1), at),
                ]

        # 트레일링 — 오른 만큼 손절가를 올린다
        for sid, pos in open_positions.items():
            trail = round(prices[sid][day] - 2.2 * pos["risk"], 0)
            if trail > pos["stop"]:
                pos["stop"] = trail

    # 아직 열려 있는 것들
    today = days[-1]
    for sid, pos in open_positions.items():
        positions.append((pos["pid"], sid, pos["market"], pos["qty"], pos["avg"], pos["did"],
                          pos["entry_date"], pos["init_stop"], pos["stop"], pos["risk"],
                          pos["stop"] > pos["avg"], None, "open",
                          ts(pos["entry_date"], 14, 31), ts(today, 14, 31)))

    # 워치리스트 점수 — ①의 평가손익이 "마지막 사이클이 본 가격"을 여기서 읽는다
    for sid, _, _, _ in SYMBOLS:
        cscores[(cycle_ids[today], sid)] = (
            cycle_ids[today], sid, "holding" if sid in open_positions else "topRank",
            round(rng.uniform(0.4, 0.9), 4), rng.uniform(0.2, 0.95),
            round(rng.uniform(0.4, 0.95), 4), round(prices[sid][today], 0),
            rng.randint(100, 9000), rng.randint(100, 9000),
            round(prices[sid][today] * 0.03, 0),
            round(prices[sid][today] * 0.06, 0), True, None, ts(today, 14, 30))

    many(
        conn,
        'INSERT INTO decisions (decision_id,cycle_id,symbol_id,action,reason,score,'
        'threshold,entry_price,stop_price,risk_per_share,target_positions,quantity,'
        'reward_risk_ratio,estimated_cost,net_edge,regime,decided_date_time) '
        "VALUES (" + ",".join(["%s"] * 17) + ")", decisions)
    many(
        conn,
        'INSERT INTO orders (client_order_id,cycle_id,decision_id,kis_order_no,symbol_id,'
        'side,purpose,order_type,order_quantity,order_price,trigger_price,'
        'filled_quantity,average_fill_price,fee,tax,status,ordered_date_time,'
        'filled_date_time,mode) VALUES (' + ",".join(["%s"] * 19) + ")", orders)
    many(
        conn,
        'INSERT INTO positions (position_id,symbol_id,market,quantity,average_price,'
        'entry_decision_id,entry_date,initial_stop_price,current_stop_price,risk_per_share,'
        'is_breakeven_done,active_stop_order_id,status,opened_date_time,updated_date_time) '
        "VALUES (" + ",".join(["%s"] * 15) + ")", positions)
    many(
        conn,
        'INSERT INTO outcomes (outcome_id,position_id,entry_decision_id,exit_decision_id,'
        'symbol_id,entry_price,exit_price,quantity,entry_date,exit_date,holding_days,'
        'gross_profit_loss,fee,tax,net_profit_loss,return_percent,r_multiple,exit_kind,'
        'exit_reason,entry_score,entry_score_bucket,entry_regime,closed_date_time,mode) '
        "VALUES (" + ",".join(["%s"] * 24) + ")", outcomes)
    many(
        conn,
        'INSERT INTO risk_checks (check_id,cycle_id,decision_id,check_order,check_name,'
        'result,reason,limit_value,actual_value,checked_date_time) '
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", checks)
    many(
        conn,
        'INSERT INTO cycle_scores (cycle_id,symbol_id,inclusion,base_score,'
        'flow_percentile_live,total_score,last_price,buy_quantity,sell_quantity,atr,'
        'stop_width,is_tradable,block_reason,scored_date_time) '
        "VALUES (" + ",".join(["%s"] * 14) + ")", list(cscores.values()))

    # ── 입출금 ──────────────────────────────────────────────
    # ②의 마커와 ③의 섞인 행, ④의 미분류 항목을 한 번에 보여주는 시나리오
    flows = [
        (days[40], "deposit", 500_000, "confirmed", "manual"),
        (days[120], "deposit", 1_000_000, "confirmed", "residual"),
        (days[190], "withdrawal", -300_000, "confirmed", "manual"),
        (days[-6], "deposit", 200_000, "unconfirmed", "residual"),
    ]
    flow_rows = []
    for n, (day, kind, amount, stat, source) in enumerate(flows):
        flow_rows.append((f"F{n}", cycle_ids[day], day, kind, amount, stat, source,
                          1_200_000, 1_200_000 + amount, None, ts(day, 14, 31),
                          ts(day, 15, 0) if stat == "confirmed" else None,
                          "owner" if stat == "confirmed" else None, "paper"))
    many(
        conn,
        'INSERT INTO cash_flows (flow_id,detected_cycle_id,trade_date,kind,amount,'
        'status,source,expected_cash,actual_cash,note,detected_date_time,'
        'confirmed_date_time,confirmed_by,mode) VALUES (' + ",".join(["%s"] * 14) + ")",
        flow_rows)

    # ── 계좌 스냅샷 ─────────────────────────────────────────
    # 총자본 − 누적 순입금 = 누적 손익이 실제로 맞아떨어지게 실현손익에서 역산한다.
    flow_by_day = {day: amt for day, _, amt, _, _ in flows}
    snaps = []
    cum_flow = float(START_CAPITAL)
    cum_realized = 0.0
    twr = 1.0
    prev_total = float(START_CAPITAL)
    for i, day in enumerate(days):
        flow = float(flow_by_day.get(day, 0))
        cum_flow += flow
        cum_realized += realized_by_day.get(day, 0.0)
        # 미실현은 그날 보유의 평가차익 — 데모라 완만한 곡선으로 근사한다
        unrealized = sum(
            (prices[sid][day] - p["avg"]) * p["qty"]
            for sid, p in open_positions.items()
            if p["entry_i"] <= i
        ) if i == len(days) - 1 else cum_realized * 0.12
        total = cum_flow + cum_realized + unrealized
        base = prev_total
        adjusted = base + flow
        day_ret = total / adjusted - 1 if adjusted else 0.0
        twr *= 1 + day_ret
        position_value = sum(
            prices[sid][day] * p["qty"] for sid, p in open_positions.items()
        ) if i == len(days) - 1 else max(0.0, total * 0.72)
        snaps.append((f"S{day.isoformat()}", cycle_ids[day], day,
                      round(total - position_value, 0), round(position_value, 0),
                      round(total, 0), round(base, 0), flow, round(adjusted, 0),
                      round(cum_flow, 0), twr, day_ret, ts(day, 14, 32)))
        prev_total = total
    many(
        conn,
        'INSERT INTO account_snapshots (snapshot_id,cycle_id,trade_date,amount,'
        'position_value,total_asset,base_asset,net_flow_since_base,adjusted_base_asset,'
        'cumulative_net_flow,twr_index,day_return_percent,recorded_date_time) '
        "VALUES (" + ",".join(["%s"] * 13) + ")", snaps)

    # ── ④가 비어 있지 않게 ───────────────────────────────────
    many(
        conn,
        'INSERT INTO safe_stop_events (event_id,cycle_id,occurred_date_time,cause,'
        'trigger,released_date_time,released_by,release_reason) '
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        [
            ("E1", cycle_ids[days[-9]], ts(days[-9], 14, 31),
             "balanceSync: 보유 수량 불일치 (005930 우리 12주 / KIS 10주)", "auto",
             ts(days[-9], 18, 0), "owner", "KIS 잔고 확인 후 Positions 정정"),
            ("E2", cycle_ids[days[-2]], ts(days[-2], 14, 31),
             "dataFreshness: DailyBars가 2거래일 낡음", "auto", None, None, None),
        ],
    )
    many(
        conn,
        'INSERT INTO ingest_runs (run_id,target_table,source,range_start_date,'
        'range_end_date,status,target_count,success_count,rows_written,error_message,'
        'started_date_time,finished_date_time) VALUES (' + ",".join(["%s"] * 12) + ")",
        [
            ("R1", "daily_bars", "KIS", days[-2], days[-2], "partial", 2_412, 2_180, 2_180,
             "rate limit 초과로 232종목 미수집", ts(days[-2], 7, 0), ts(days[-2], 8, 12)),
            ("R2", "daily_flows", "KIS", days[-1], days[-1], "failed", 2_412, 0, 0,
             "KIS 인증 토큰 발급 실패 (EGW00133)", ts(days[-1], 7, 0), ts(days[-1], 7, 1)),
        ],
    )
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description="대시보드 화면 개발용 데모 서버")
    ap.add_argument("--port", type=int, default=API_PORT)
    ap.add_argument("--password", default=DEV_PASSWORD)
    ap.add_argument("--reset", action="store_true", help="데모 데이터를 다시 만든다")
    args = ap.parse_args()

    import pgserver

    data_dir = Path(tempfile.gettempdir()) / "alphaloop-devdash"
    data_dir.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(data_dir)
    dsn = server.get_uri()

    # 설정을 통째로 데모 쪽으로 돌린다 — 실제 .env의 DB나 비밀번호를 건드리지 않는다
    os.environ["DB_DSN"] = dsn
    os.environ["DB_DSN_READONLY"] = dsn
    os.environ["DASHBOARD_INSECURE_COOKIE"] = "1"   # localhost는 http라 Secure 쿠키가 안 붙는다

    from config.settings import get_settings
    from dashboard import auth
    from memory.db import init_db

    os.environ["DASHBOARD_PASSWORD_HASH"] = auth.make_hash(args.password)
    os.environ["DASHBOARD_TOKEN_SECRET"] = "dev-only-secret-not-for-production-use-32b+"
    get_settings.cache_clear()

    conn = init_db(dsn)
    seeded = conn.execute('SELECT count(*) AS n FROM account_snapshots').fetchone()["n"]
    if args.reset or seeded == 0:
        if seeded:
            for table in ("outcomes", "positions", "orders", "risk_checks", "decisions",
                          "cycle_scores", "cash_flows", "account_snapshots", "safe_stop_events",
                          "cycles", "daily_scores", "daily_flows", "daily_bars",
                          "market_indices", "ingest_runs", "symbol_states", "symbols"):
                conn.execute(f'DELETE FROM "{table}"')
            conn.commit()
        print("데모 데이터를 채우는 중…")
        seed(conn)
    conn.close()

    print(f"\n  DB       {dsn}")
    print(f"  API      http://127.0.0.1:{args.port}")
    print(f"  비밀번호  {args.password}")
    print("  화면     cd dashboard/web && npm run dev  →  http://localhost:5173\n")

    import uvicorn

    uvicorn.run("dashboard.api:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
