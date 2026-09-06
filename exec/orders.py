"""
description:        주문 송출·집행 (신규 진입 → KIS 송출 → Orders·Positions 적재)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from dataclasses import dataclass
from typing import Protocol

from core.timeutils import now_utc
from memory import journal
from ops import notify

# 진입 주문구분 — 모드별(모의 IOC 미지원 보정). if-분기 아닌 데이터 룩업.
ENTRY_ORD_DVSN = {"real": "11", "paper": "00", "backtest": "00"}
STOP_ORD_DVSN = "22"   # 손절 스톱지정가. 트리거 도달 시 KIS 자동 발동.
# 브로커가 스톱을 받아들였다고 볼 상태. 그 밖(rejected 등)이면 맨몸 포지션이다.
_STOP_ACCEPTED = frozenset({"submitted", "filled", "partial"})


@dataclass
class Fill:
    """broker가 정규화한 체결 결과."""
    filled_qty: int
    fill_price: float | None
    status: str                       # submitted/filled/partial/cancelled/rejected
    broker_order_id: str | None = None
    fee: float | None = None
    tax: float | None = None


class Broker(Protocol):
    """주문 집행 채널. KISClient(실거래·모의)·FakeBroker(테스트)가 구현한다."""
    def place_entry(
        self, *, code: str, qty: int, price: int, ord_dvsn: str, client_order_id: str
    ) -> Fill: ...

    def place_stop(
        self, *, code: str, qty: int, trigger_price: int, limit_price: int,
        client_order_id: str,
    ) -> Fill: ...

    def place_exit(
        self, *, code: str, qty: int, ord_dvsn: str, client_order_id: str
    ) -> Fill: ...


def execute_entries(
    conn,
    planned,
    *,
    broker: Broker,
    cycle_id: str,
    decision_ids: dict[str, str] | None = None,
    market_map: dict[str, str] | None = None,
    order_mode: str = "paper",
    mode: str = "paper",
    now=None,
) -> list[str]:
    """신규 진입(planned)을 송출·체결해 Orders·Positions에 적재한다. 반환: ClientOrderId 목록."""
    decision_ids = decision_ids or {}
    market_map = market_map or {}
    ord_dvsn = ENTRY_ORD_DVSN[order_mode]
    ts = now or now_utc()
    order_ids: list[str] = []
    for seq, o in enumerate(planned):
        coid = f"{cycle_id}-{o.code}-buy-{seq}"
        did = decision_ids.get(o.code)
        order_price = int(round(o.price))
        fill = broker.place_entry(
            code=o.code, qty=o.qty, price=order_price,
            ord_dvsn=ord_dvsn, client_order_id=coid,
        )
        journal.record_order(
            conn, client_order_id=coid, cycle_id=cycle_id, decision_id=did,
            symbol_id=o.code, side="buy", purpose="entry", order_type=ord_dvsn,
            order_quantity=o.qty, filled_quantity=fill.filled_qty,
            order_price=float(order_price), average_fill_price=fill.fill_price,
            kis_order_no=fill.broker_order_id, fee=fill.fee, tax=fill.tax,
            status=fill.status, mode=mode, ordered_at=ts,
            filled_at=ts if fill.filled_qty > 0 else None,
        )
        order_ids.append(coid)
        if fill.filled_qty > 0:
            # 체결가 미파싱 시 주문가로 폴백 — 체결된 포지션 장부 누락을 막는다
            entry_px = fill.fill_price if fill.fill_price is not None else float(order_price)
            position_id = journal.upsert_entry_position(
                conn, cycle_id=cycle_id, symbol_id=o.code, add_quantity=fill.filled_qty,
                fill_price=entry_px, entry_decision_id=did,
                # 진입 시 initial=current — 이후 트레일링이 올라가도 R 산정 기준은 고정된다
                current_stop_price=o.stop, initial_stop_price=o.stop,
                market=market_map.get(o.code),
            )
            # 손절 스톱 KIS 등록 — 체결 즉시 등록해 장간 갭 맨몸 포지션을 막는다
            stop_coid = _register_stop(
                conn, o, fill.filled_qty, cycle_id, seq, did, mode, ts, broker
            )
            journal.set_active_stop(conn, position_id, stop_coid)
            order_ids.append(stop_coid)
    return order_ids


def _register_stop(conn, o, filled_qty, cycle_id, seq, did, mode, ts, broker) -> str:
    """체결 수량만큼 손절 스톱지정가(22)를 등록하고 Orders에 적재. 반환: 스톱 ClientOrderId."""
    stop_coid = f"{cycle_id}-{o.code}-stop-{seq}"
    stop = int(round(o.stop))
    sf = broker.place_stop(
        code=o.code, qty=filled_qty, trigger_price=stop, limit_price=stop,
        client_order_id=stop_coid,
    )
    journal.record_order(
        conn, client_order_id=stop_coid, cycle_id=cycle_id, decision_id=did,
        symbol_id=o.code, side="sell", purpose="stop", order_type=STOP_ORD_DVSN,
        order_quantity=filled_qty, filled_quantity=0, order_price=float(stop),
        trigger_price=float(stop), kis_order_no=sf.broker_order_id,
        status=sf.status, mode=mode, ordered_at=ts,
    )
    # 스톱이 서지 않았는데 조용히 넘어가면 장 마감 후 갭에 맨몸으로 노출된다.
    # 매매를 멈추지는 않되(이미 체결된 진입은 되돌릴 수 없다) 사람을 즉시 부른다.
    if sf.status not in _STOP_ACCEPTED:
        notify.notify_stop_not_registered(o.code, filled_qty, float(stop), sf.status)
    return stop_coid
