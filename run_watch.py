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
from ops import notify

# KIS에 살아 있다고 볼 주문 상태 — 체결·취소·거부는 '없는 것'으로 친다.
_ALIVE = {"submitted", "partial"}


def load_open_positions(conn) -> list[dict]:
    """open 보유(잔량>0)를 조회한다 — 감시 대상."""
    return conn.execute(
        'SELECT p.position_id, p.symbol_id, p.quantity, p.current_stop_price, '
        'p.active_stop_order_id, o.trigger_price AS broker_stop_price, '
        'o.kis_order_no, o.kis_order_org_no '
        'FROM positions p '
        'LEFT JOIN orders o ON o.client_order_id = p.active_stop_order_id '
        "WHERE p.status='open' AND p.quantity > 0"
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
        if p["active_stop_order_id"] is None:
            missing.append(p)
        elif orders and p["symbol_id"] not in live_by_code:
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
        stop = p["current_stop_price"]
        if stop is None or stop <= 0:
            continue                    # 손절가를 모르면 임의로 만들지 않는다
        coid = f"watch{stamp}-{p['symbol_id']}-stop-0"
        if dry_run:
            ids.append(coid)
            continue
        trigger = int(round(float(stop)))
        fill = client.place_stop(
            code=p["symbol_id"], qty=p["quantity"], trigger_price=trigger,
            limit_price=trigger, client_order_id=coid,
        )
        journal.record_order(
            conn, client_order_id=coid, cycle_id=None, decision_id=None,
            symbol_id=p["symbol_id"], side="sell", purpose="stop",
            order_type=STOP_ORD_DVSN, order_quantity=p["quantity"],
            filled_quantity=0, order_price=float(trigger), trigger_price=float(trigger),
            kis_order_no=fill.broker_order_id, status=fill.status, mode=mode,
        )
        journal.set_active_stop(conn, p["position_id"], coid)
        ids.append(coid)
    return ids


def find_stale_stops(positions: list[dict]) -> list[dict]:
    """장부의 손절선과 KIS에 걸린 발동가가 어긋난 보유를 찾는다.

    트레일링·본전 상향이 브로커에 반영되지 않으면 밤사이 갭에서 옛 가격으로 체결된다.
    존재만 보는 `find_missing_stops`는 이 어긋남을 잡지 못한다(10-ops 10.13).
    """
    stale = []
    for p in positions:
        want, have = p["current_stop_price"], p["broker_stop_price"]
        if want is None or have is None:
            continue
        if abs(float(want) - float(have)) >= 1.0:      # 원 단위 — 반올림 차이는 무시
            stale.append(p)
    return stale


def revise_stale_stops(conn, client: KISClient, stale: list[dict], *,
                       dry_run: bool) -> list[str]:
    """어긋난 손절 예약을 장부 값으로 정정한다. 반환: 정정한 종목 목록."""
    done: list[str] = []
    for p in stale:
        if not p["kis_order_no"] or not p["kis_order_org_no"]:
            print(f"  {p['symbol_id']}: KIS 식별자가 없어 정정 불가(옛 주문)")
            continue
        trigger = int(round(float(p["current_stop_price"])))
        if dry_run:
            done.append(p["symbol_id"])
            continue
        fill = client.revise_stop(
            code=p["symbol_id"], qty=int(p["quantity"]),
            orgn_odno=str(p["kis_order_no"]), org_no=str(p["kis_order_org_no"]),
            trigger_price=trigger, limit_price=trigger,
        )
        if fill.status in ("submitted", "filled", "partial"):
            journal.record_stop_revision(
                conn, client_order_id=p["active_stop_order_id"],
                trigger_price=float(trigger), kis_order_no=fill.broker_order_id,
                kis_order_org_no=fill.broker_org_no,
            )
            done.append(p["symbol_id"])
        else:
            notify.notify_stop_not_revised(
                p["symbol_id"], float(trigger), f"감시 정정 실패({fill.status})")
    return done


def find_stop_gaps(client: KISClient, positions: list[dict]) -> list:
    """현재가가 손절선을 이탈했는데 아직 보유 중인 종목(손절 구멍)을 찾는다."""
    prices: dict[str, float] = {}
    for p in positions:
        try:
            out = client.get_price(p["symbol_id"]).get("output", {})
            prices[p["symbol_id"]] = float(out.get("stck_prpr") or 0)
        except Exception:
            continue                    # 결측은 다음 폴링에서 재시도
    watch = [
        StopPosition(p["symbol_id"], float(p["current_stop_price"] or 0), p["quantity"])
        for p in positions if p["current_stop_price"]
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
        codes = [p["symbol_id"] for p in missing]
        print(f"  손절 없는 보유 {len(missing)}종목: {codes}")
        ids = register_missing_stops(conn, client, missing, dry_run=args.check)
        print(f"  {'등록 예정' if args.check else '등록 완료'} {len(ids)}건")
    else:
        print("  ① 상주 스톱 정상")

    # ② 손절선 어긋남 — 장부는 올렸는데 KIS 예약이 옛 가격인 경우
    stale = find_stale_stops(positions)
    if stale:
        for p in stale:
            print(f"  손절 어긋남 {p['symbol_id']}: 장부 {p['current_stop_price']:,.0f} "
                  f"≠ KIS {p['broker_stop_price']:,.0f}")
        fixed = revise_stale_stops(conn, client, stale, dry_run=args.check)
        print(f"  {'정정 예정' if args.check else '정정 완료'} {len(fixed)}건")
    else:
        print("  ② 손절선 일치")

    # ③ 손절 구멍
    hits = find_stop_gaps(client, positions)
    if hits:
        for h in hits:
            print(f"  ③ 손절 구멍 {h.symbol}: 현재가 {h.price:,.0f} ≤ 손절 {h.stop:,.0f}")
        print("  → 정리는 사이클의 청산 경로가 한다(run_cycle)")
    else:
        print("  ③ 손절 구멍 없음")

    if args.check:
        print("\n점검 모드 — 주문을 내지 않았다")
    conn.close()


if __name__ == "__main__":
    main()
