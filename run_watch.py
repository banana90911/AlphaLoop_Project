"""
description:        보유 감시 진입점 (장중 30분 간격, 손절 무결성만 확인)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import argparse

from broker.kis_client import KISClient
from config.settings import get_settings
from core.timeutils import kst_today, now_utc
from core.trading_days import is_session_open
from exec.exits import StopPosition, detect_stop_gaps
from exec.orders import STOP_ORD_DVSN
from memory import journal
from memory.db import init_db

# KIS에 살아 있다고 볼 주문 상태 — 체결·취소·거부는 '없는 것'으로 친다.
_ALIVE = {"submitted", "partial"}


def load_open_positions(conn) -> list[dict]:
    """open 보유(잔량>0)를 조회한다 — 감시 대상."""
    return conn.execute(
        'SELECT "PositionId", "SymbolId", "Quantity", "CurrentStopPrice", '
        '"ActiveStopOrderId" FROM "Positions" '
        "WHERE \"Status\"='open' AND \"Quantity\" > 0"
    ).fetchall()


def find_missing_stops(conn, client: KISClient, positions: list[dict]) -> list[dict]:
    """상주 스톱이 없거나 KIS에서 이미 사라진 보유를 찾는다."""
    try:
        orders = client.get_daily_orders(kst_today().strftime("%Y%m%d"))
    except Exception:
        orders = []                    # 조회 실패 시 장부만 보고 판단(과잉 등록보다 낫다)
    live_by_code = {
        o.get("pdno") for o in orders
        if o.get("ord_dvsn_cd") == STOP_ORD_DVSN and _is_alive(o)
    }
    missing = []
    for p in positions:
        if p["ActiveStopOrderId"] is None:
            missing.append(p)
        elif orders and p["SymbolId"] not in live_by_code:
            missing.append(p)          # 장부엔 있는데 KIS엔 없다
    return missing


def _is_alive(order: dict) -> bool:
    """KIS 일별주문 한 건이 아직 살아 있는지(취소되지 않고 잔량 있음) 판정한다."""
    try:
        ordered = int(order.get("ord_qty") or 0)
        filled = int(order.get("tot_ccld_qty") or 0)
    except (TypeError, ValueError):
        return False
    cancelled = (order.get("cncl_yn") or "N").upper() == "Y"
    return not cancelled and ordered > filled


def register_missing_stops(conn, client: KISClient, missing: list[dict], *,
                           dry_run: bool) -> list[str]:
    """빠진 스톱을 다시 등록한다. 반환: 등록한 ClientOrderId 목록."""
    ids: list[str] = []
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    mode = get_settings().trading_mode
    for p in missing:
        stop = p["CurrentStopPrice"]
        if stop is None or stop <= 0:
            continue                    # 손절가를 모르면 임의로 만들지 않는다
        coid = f"watch{stamp}-{p['SymbolId']}-stop-0"
        if dry_run:
            ids.append(coid)
            continue
        trigger = int(round(float(stop)))
        fill = client.place_stop(
            code=p["SymbolId"], qty=p["Quantity"], trigger_price=trigger,
            limit_price=trigger, client_order_id=coid,
        )
        journal.record_order(
            conn, client_order_id=coid, cycle_id=None, decision_id=None,
            symbol_id=p["SymbolId"], side="sell", purpose="stop",
            order_type=STOP_ORD_DVSN, order_quantity=p["Quantity"],
            filled_quantity=0, order_price=float(trigger), trigger_price=float(trigger),
            kis_order_no=fill.broker_order_id, status=fill.status, mode=mode,
        )
        journal.set_active_stop(conn, p["PositionId"], coid)
        ids.append(coid)
    return ids


def find_stop_gaps(client: KISClient, positions: list[dict]) -> list:
    """현재가가 손절선을 이탈했는데 아직 보유 중인 종목(손절 구멍)을 찾는다."""
    prices: dict[str, float] = {}
    for p in positions:
        try:
            out = client.get_price(p["SymbolId"]).get("output", {})
            prices[p["SymbolId"]] = float(out.get("stck_prpr") or 0)
        except Exception:
            continue                    # 결측은 다음 폴링에서 재시도
    watch = [
        StopPosition(p["SymbolId"], float(p["CurrentStopPrice"] or 0), p["Quantity"])
        for p in positions if p["CurrentStopPrice"]
    ]
    return detect_stop_gaps(watch, prices)


def main() -> None:
    """CLI 진입점 — 상주 스톱 무결성과 손절 구멍을 점검한다."""
    ap = argparse.ArgumentParser(description="AlphaLoop 보유 감시")
    ap.add_argument("--check", action="store_true",
                    help="무엇을 할지 보고만 하고 주문은 내지 않는다")
    ap.add_argument("--force", action="store_true",
                    help="장이 닫혀 있어도 점검한다(진단용)")
    args = ap.parse_args()

    if not is_session_open() and not args.force:
        print("장 시간이 아니다 — 감시하지 않는다 (--force로 점검만 가능)")
        return

    mode = get_settings().trading_mode
    conn = init_db()
    client = KISClient(mode=mode)

    positions = load_open_positions(conn)
    if not positions:
        print("보유 없음 — 감시할 대상이 없다")
        conn.close()
        return
    print(f"[{mode}] 보유 {len(positions)}종목 감시")

    # ① 상주 스톱 무결성
    missing = find_missing_stops(conn, client, positions)
    if missing:
        codes = [p["SymbolId"] for p in missing]
        print(f"  손절 없는 보유 {len(missing)}종목: {codes}")
        ids = register_missing_stops(conn, client, missing, dry_run=args.check)
        print(f"  {'등록 예정' if args.check else '등록 완료'} {len(ids)}건")
    else:
        print("  ① 상주 스톱 정상")

    # ② 손절 구멍
    hits = find_stop_gaps(client, positions)
    if hits:
        for h in hits:
            print(f"  ② 손절 구멍 {h.symbol}: 현재가 {h.price:,.0f} ≤ 손절 {h.stop:,.0f}")
        print("  → 정리는 사이클의 청산 경로가 한다(run_cycle)")
    else:
        print("  ② 손절 구멍 없음")

    if args.check:
        print("\n점검 모드 — 주문을 내지 않았다")
    conn.close()


if __name__ == "__main__":
    main()
