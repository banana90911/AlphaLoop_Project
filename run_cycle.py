"""
description:        사이클 실행
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import argparse

from broker.kis_client import KISClient
from config.settings import get_settings
from core.timeutils import kst_today
from core.trading_days import previous_trading_day
from data import market_data
from data.sources import universe
from memory import journal
from memory.db import init_db
from ops import heartbeat, notify
from pipeline import cycle, gates
from risk.risk_engine import Account, Position

# 252거래일 ≈ 달력 365일, 여기에 20거래일 스킵과 휴장 여유를 얹어 450일로 둔다.
LOOKBACK_DAYS = 450


def build_account(client: KISClient, market_map: dict[str, str]) -> tuple[Account, dict[str, int]]:
    """KIS 잔고 -> Account + {종목코드: 보유수량}"""
    balance = client.fetch_balance()

    positions = [
        Position(holding.code, holding.qty, holding.last_price,
                 market_map.get(holding.code, "KOSPI"))
        for holding in balance.holdings
    ]
    account = Account(start_capital=balance.base_asset, cash=balance.cash,
                      positions=positions)

    return account, {holding.code: holding.qty for holding in balance.holdings}


def main() -> None:
    """CLI 진입점 — 잔고·시세·게이트 입력을 모아 cycle.run에 넘기고 결과를 출력한다."""
    ap = argparse.ArgumentParser(description="AlphaLoop 매매 사이클")
    ap.add_argument("--codes", nargs="*", default=None,
                    help="후보 종목코드. 생략 시 당일 DailyScores 통과 종목 전체(+보유 종목)")
    ap.add_argument("--live", action="store_true",
                    help="실제 주문 송출(모의 모드에서만). 없으면 집행 계획까지만")
    ap.add_argument("--skip-freshness", action="store_true",
                    help="데이터 신선도 검사를 건너뛴다(진단용 — 운영에서 쓰지 말 것)")
    args = ap.parse_args()

    mode = get_settings().trading_mode
    if args.live and mode != "paper":
        raise SystemExit(f"--live는 모의(paper)에서만 허용한다 (현재 mode={mode!r})")

    conn = init_db()
    heartbeat.ping_start()
    recovered = journal.recover_pending_cycles(conn)
    if recovered:
        print(f"미완 사이클 복구(failed): {recovered}")

    client = KISClient(mode=mode)
    market_map = universe.load_market_map()
    account, kis_holdings = build_account(client, market_map)
    holdings = list(kis_holdings)
    print(f"[{mode}] 예수금 {account.cash:,.0f}원 · 총자본 {account.equity:,.0f}원 "
          f"· 보유 {len(holdings)}종목")

    # 지표는 전일 확정 봉으로, 현재가는 사이클 시점 조회로 — 둘을 섞지 않는다(04-data 4.2)
    today = kst_today()
    asof = previous_trading_day(today)
    print(f"지표 기준일 {asof} · 사이클 날짜 {today}")

    # 4단계 게이트 입력 — 잔고 대조·거래일·데이터 신선도를 실제로 재서 넣는다
    market_state, notes = gates.build_market_state(
        conn, kis_holdings=kis_holdings, trade_date=today, client=client,
        check_data=not args.skip_freshness,
    )
    if notes:
        print(f"게이트 입력: {notes}")

    codes = args.codes
    if codes is None:
        candidates = journal.load_daily_score_candidates(conn, asof)
        if not candidates:
            raise SystemExit(
                "당일 DailyScores가 비어 있다 — run_daily_ingest.py를 먼저 돌릴 것"
            )
        codes = sorted(set(candidates) | set(holdings))  # 보유 종목은 항상 포함
        print(f"워치리스트 후보 {len(candidates)}종목(배치) + 보유 {len(holdings)}종목")

    prices, failed = market_data.fetch_prices(
        codes, mode=mode, lookback_days=LOOKBACK_DAYS, client=client
    )
    if failed:
        print(f"시세 실패 {len(failed)}건: {[c for c, _ in failed]}")
    if not prices:
        raise SystemExit("시세를 한 종목도 받지 못해 사이클을 돌리지 않는다")

    res = cycle.run(
        conn,
        market_data=prices,
        holdings=tuple(holdings),
        account=account,
        market_state=market_state,
        asof=asof,
        trade_date=today,
        quote_fetcher=lambda wl: gates.fetch_quotes(client, wl),
        market_map=market_map,
        broker=client if args.live else None,
        mode=mode,
    )

    status = conn.execute(
        'SELECT "Status" FROM "Cycles" WHERE "CycleId"=%s', (res.cycle_id,)
    ).fetchone()["Status"]
    print(f"\n사이클 {res.cycle_id} → {status} ({res.cycle_action}"
          f"{': ' + res.blocked_reason if res.blocked_reason else ''})")

    if res.safe_stop_id:
        print(f"\n🚨 SafeStop 발생 — {res.blocked_reason}\n"
              f"   EventId: {res.safe_stop_id}\n"
              "   사람이 원인을 확인하고 해제해야 다음 사이클의 신규 진입이 열린다.")
        heartbeat.ping_safe_stop(res.blocked_reason)
    elif status in ("failed", "skipped"):
        notify.notify_cycle_failure(res.cycle_id, 4, res.blocked_reason)
        heartbeat.ping_failure(f"{status}: {res.blocked_reason}")
    else:
        heartbeat.ping_success(f"워치리스트 {len(res.watchlist)}·계획 {len(res.planned_orders)}")

    print(f"워치리스트 {len(res.watchlist)}종목: {res.watchlist}")
    if res.decision:
        for o in res.decision.orders:
            print(f"  결정 {o.code} {o.action} (점수 {o.risk_budget:.3f})")
    for p in res.planned_orders:
        print(f"  집행계획 {p.code} {p.qty}주 @ {p.price:,.0f} 손절 {p.stop:,.0f}")
    if not args.live:
        print("\n드라이런 — 주문을 내지 않았다. 실제 송출은 --live")
    elif res.order_ids:
        print(f"\n송출 {len(res.order_ids)}건: {res.order_ids}")
    conn.close()


if __name__ == "__main__":
    main()
