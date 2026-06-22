"""주문 송출·집행 — planned(신규 진입) → KIS 송출 → trades·positions 적재 (7단계).

이 모듈은 *오케스트레이션*만 한다 — client_order_id 부여, 재시도 금지 정책(11-2.3),
체결 결과의 trades·positions 적재. *KIS 통신·체결 확인·응답 정규화는 broker(Broker
프로토콜)가 책임*진다(라이브 응답 필드가 미확정이라 파싱을 한 곳에 격리). 순수 결정론
코드(LLM 미관여, 03-arch 3.3) — broker만 외부 I/O.

주문 유형(11-2.14 정책표): 진입=11 IOC지정가. 단 KIS 모의는 IOC 미지원이라 paper는
00 일반지정가로 보정(reference_kis_paper_no_ioc) — `if mode` 분기가 아니라 모드별
데이터 룩업(ENTRY_ORD_DVSN). 청산(sell/trim) 집행은 exits 경로로 후속 배선.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.timeutils import utc_iso
from memory import journal

# 진입 주문구분 — 모드별(11-2.14 + 모의 IOC 미지원 보정). if-분기 아닌 데이터 룩업.
ENTRY_ORD_DVSN = {"real": "11", "paper": "00", "backtest": "00"}
STOP_ORD_DVSN = "22"   # 손절 스톱지정가(11-2.14). 트리거 도달 시 KIS 자동 발동.


@dataclass
class Fill:
    """broker가 정규화한 체결 결과. broker가 송출·접수확인·파싱을 끝낸 단일 진실."""
    filled_qty: int
    fill_price: float | None
    status: str                       # submitted/filled/partial/cancelled/rejected
    broker_order_id: str | None = None
    fee: float | None = None
    tax: float | None = None


class Broker(Protocol):
    """주문 집행 채널. KISClient(실거래·모의)·FakeBroker(테스트)가 구현.

    place_entry는 송출 + 접수/체결 확인 + 정규화까지 책임지고 Fill을 반환한다.
    POST 재시도는 하지 않으며(중복주문 방지 11-2.3), 송출 실패·미접수는 status로 표현한다.
    """
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
    source: str = "paper",
    now: str | None = None,
) -> list[str]:
    """신규 진입(planned) 송출·체결 → trades·positions 적재. 반환: trade_id 목록.

    planned: PlannedOrder 시퀀스(code·qty·price·stop). decision_ids: code→decision_id
    (8단계 결정 적재 결과, trades.decision_id FK). market_map: code→시장(거래세율·비용,
    positions.market). 체결(filled_qty>0)이면 positions 갱신.
    """
    decision_ids = decision_ids or {}
    market_map = market_map or {}
    ord_dvsn = ENTRY_ORD_DVSN[order_mode]
    ts = now or utc_iso()
    trade_ids: list[str] = []
    for seq, o in enumerate(planned):
        coid = f"{cycle_id}-{o.code}-buy-{seq}"
        did = decision_ids.get(o.code)
        order_price = int(round(o.price))
        fill = broker.place_entry(
            code=o.code, qty=o.qty, price=order_price,
            ord_dvsn=ord_dvsn, client_order_id=coid,
        )
        journal.record_trade(
            conn, trade_id=coid, cycle_id=cycle_id, decision_id=did,
            client_order_id=coid, symbol=o.code, side="buy", ord_dvsn=ord_dvsn,
            order_qty=o.qty, filled_qty=fill.filled_qty, order_price=float(order_price),
            fill_price=fill.fill_price, fee=fill.fee, tax=fill.tax, status=fill.status,
            source=source, ordered_at=ts,
            filled_at=ts if fill.filled_qty > 0 else None,
        )
        trade_ids.append(coid)
        if fill.filled_qty > 0:
            # 체결가 미파싱(KIS avg_prvs 0/누락) 시 주문가로 폴백 — 체결된 포지션을 장부·스톱
            # 등록에서 통째로 누락(추적 안 되는 맨몸 포지션)시키지 않는다. trades엔 broker가
            # 보고한 원값(None)을 그대로 남기고, 비용·R 산정이 필요한 positions.avg_price만 보정.
            entry_px = fill.fill_price if fill.fill_price is not None else float(order_price)
            journal.upsert_entry_position(
                conn, cycle_id=cycle_id, symbol=o.code, add_qty=fill.filled_qty,
                fill_price=entry_px, entry_decision_id=did,
                current_stop_price=o.stop, initial_stop_price=o.stop,   # 진입 시 initial=current(R 고정)
                market=market_map.get(o.code),
            )
            # 손절 스톱 KIS 등록 — 체결 즉시 등록해 장간 갭 맨몸 포지션을 막는다(11-2.3).
            trade_ids.append(
                _register_stop(conn, o, fill.filled_qty, cycle_id, seq, did, source, ts, broker)
            )
    return trade_ids


def _register_stop(conn, o, filled_qty, cycle_id, seq, did, source, ts, broker) -> str:
    """체결 수량만큼 손절 스톱지정가(22)를 등록하고 trades에 적재. 반환: 스톱 trade_id."""
    stop_coid = f"{cycle_id}-{o.code}-stop-{seq}"
    stop = int(round(o.stop))
    sf = broker.place_stop(
        code=o.code, qty=filled_qty, trigger_price=stop, limit_price=stop,
        client_order_id=stop_coid,
    )
    journal.record_trade(
        conn, trade_id=stop_coid, cycle_id=cycle_id, decision_id=did,
        client_order_id=stop_coid, symbol=o.code, side="sell", ord_dvsn=STOP_ORD_DVSN,
        order_qty=filled_qty, filled_qty=0, order_price=float(stop), trigger_price=float(stop),
        status=sf.status, source=source, ordered_at=ts,
    )
    return stop_coid
