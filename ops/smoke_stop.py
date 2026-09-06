"""
description:        손절 스톱지정가(ORD_DVSN=22) 실계좌 검증
                    (모의 미지원이라 소액 실거래로만 잴 수 있다)
author:             siheon jung
created date:       2026/09/06
last modified date: 2026/09/06
remarks:            운영 DB에 아무것도 쓰지 않는다(브로커 계층만 두드린다).
                    검증이 끝나면 스톱 취소와 매도는 KIS 앱에서 사람이 직접 한다 —
                    정정취소 API(TTTC0013U)가 아직 구현돼 있지 않기 때문이다.
"""

import argparse
import sys

from broker.kis_client import KISClient, KISError
from config.settings import get_settings
from core.timeutils import kst_today, now_utc
from exec.orders import STOP_ORD_DVSN

# 실계좌를 두드리는 스크립트다. 사고를 금액으로 막는다 — 기본 상한을 넘으면 아예 송출하지 않는다.
DEFAULT_MAX_AMOUNT = 50_000


def fetch_price(client: KISClient, code: str) -> int:
    """현재가 1건을 원 단위 정수로 반환한다."""
    out = client.get_price(code).get("output", {})
    price = int(float(out.get("stck_prpr") or 0))
    if price <= 0:
        raise SystemExit(f"현재가를 받지 못했다: {code}")
    return price


def main() -> None:
    """CLI 진입점 — 1주 매수 → 스톱 등록 → 등록 확인까지 실행한다."""
    ap = argparse.ArgumentParser(description="손절 스톱지정가 실계좌 스모크")
    ap.add_argument("--code", required=True, help="검증에 쓸 종목코드(저가주 권장)")
    ap.add_argument("--qty", type=int, default=1, help="수량(기본 1주)")
    ap.add_argument("--stop-pct", type=float, default=0.05,
                    help="체결가 대비 손절 트리거 하락률(기본 5%%). 즉시 발동하지 않게 아래로 둔다")
    ap.add_argument("--max-amount", type=int, default=DEFAULT_MAX_AMOUNT,
                    help=f"주문 금액 상한(원). 넘으면 송출하지 않는다(기본 {DEFAULT_MAX_AMOUNT:,})")
    ap.add_argument("--execute", action="store_true",
                    help="실제 주문을 낸다. 없으면 계획만 보여준다")
    args = ap.parse_args()

    mode = get_settings().trading_mode
    client = KISClient(mode=mode)

    price = fetch_price(client, args.code)
    amount = price * args.qty
    trigger = int(round(price * (1 - args.stop_pct)))

    print(f"[{mode}] {args.code} 현재가 {price:,}원 × {args.qty}주 = {amount:,}원")
    print(f"       손절 트리거 {trigger:,}원 (−{args.stop_pct:.1%})")

    if amount > args.max_amount:
        raise SystemExit(f"주문 금액 {amount:,}원이 상한 {args.max_amount:,}원을 넘는다 — 중단한다")
    if not args.execute:
        print("\n계획만 표시했다. 실제 송출은 --execute")
        return
    if mode != "real":
        print(f"\n경고: 현재 모드가 {mode!r}다. 스톱지정가(22)는 모의에서 거부된다 "
              "— 그 거부를 확인하는 것이 목적이 아니라면 실전 모드로 돌릴 것")

    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")

    # ① 진입 — 지정가로 현재가에 산다(IOC는 실전 전용이라 여기서도 실전 기준으로 간다)
    print("\n① 매수 송출…")
    entry = client.place_entry(
        code=args.code, qty=args.qty, price=price,
        ord_dvsn="11", client_order_id=f"smoke{stamp}-entry",
    )
    print(f"   상태 {entry.status} · 체결 {entry.filled_qty}주 · 주문번호 {entry.broker_order_id}")
    if entry.filled_qty <= 0:
        raise SystemExit("체결되지 않아 스톱 검증을 못 한다 — 호가를 확인하고 다시 시도할 것")

    # ② 손절 스톱 등록 — 이 한 줄이 이 스크립트의 존재 이유다
    print("\n② 손절 스톱(ORD_DVSN=22) 등록…")
    try:
        stop = client.place_stop(
            code=args.code, qty=entry.filled_qty, trigger_price=trigger,
            limit_price=trigger, client_order_id=f"smoke{stamp}-stop",
        )
        print(f"   상태 {stop.status} · 주문번호 {stop.broker_order_id}")
    except KISError as e:
        print(f"   ✗ 거부됨: {e}")
        stop = None

    # ③ KIS가 실제로 받아들였는지 일별주문조회로 되짚는다 — 응답만 믿지 않는다
    print("\n③ 일별주문조회로 확인…")
    rows = client.get_daily_orders(kst_today().strftime("%Y%m%d"))
    mine = [r for r in rows if r.get("pdno") == args.code]
    for r in mine:
        print(f"   주문 {r.get('odno')} 구분 {r.get('ord_dvsn_cd')} "
              f"수량 {r.get('ord_qty')} 체결 {r.get('tot_ccld_qty')} 취소 {r.get('cncl_yn')}")
    live_stop = [r for r in mine if r.get("ord_dvsn_cd") == STOP_ORD_DVSN]

    print("\n" + "=" * 60)
    if live_stop:
        print("✓ 스톱지정가(22)가 실계좌에서 받아들여졌다 — 손절 예약 경로가 동작한다")
    else:
        print("✗ 스톱지정가(22)가 조회에 잡히지 않는다 — place_stop의 CNDT_PRIC 방식을 재검토할 것")
    print("정리는 사람이 한다: KIS 앱에서 ① 스톱 예약 취소 → ② 보유분 매도")
    print("=" * 60)
    sys.exit(0 if live_stop else 1)


if __name__ == "__main__":
    main()
