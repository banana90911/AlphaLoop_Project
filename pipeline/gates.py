"""
description:        사이클 게이트 입력 조립 (잔고 대조·데이터 신선도·종목 상태)
author:             siheon jung
created date:       2026/08/30
last modified date: 2026/08/30
remarks:            판정은 risk_engine이 한다. 이 모듈은 판정에 넣을 사실만 모은다.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from core.timeutils import kst_today
from core.trading_days import is_trading_day
from data import corrections
from risk.risk_engine import MarketState, StockStatus

log = logging.getLogger(__name__)

# KIS 현재가(inquire-price) 종목상태구분코드 → 신규 진입 금지 사유 (05-risk 5.1).
# 54(투자주의)는 경고·위험보다 약한 단계라 진입을 막지 않는다.
_STAT_SUSPENDED = {"51", "52", "53", "58"}   # 관리·투자위험·투자경고·거래정지
_STAT_OVERHEATED = {"59"}                    # 단기과열(3거래일 30분 단일가)

# vi_cls_code·temp_stop_yn이 '아니다'를 뜻하는 값. 이 밖의 값은 발동으로 본다
# (모르는 값을 정상으로 넘기는 쪽이 위험하므로 보수적으로 막는다).
_NEGATIVE = {"", "n", "0", "00", "none"}


def _is_on(value: Any) -> bool:
    """KIS 플래그 문자열이 '발동/해당'인지 판정한다(모르는 값은 발동으로 본다)."""
    if value is None:
        return False
    return str(value).strip().lower() not in _NEGATIVE


def _num(value: Any) -> float | None:
    """KIS 숫자 문자열을 float으로 바꾼다(비었거나 파싱 불가면 None)."""
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def stock_status(quote: dict[str, Any]) -> StockStatus:
    """KIS 현재가 응답 한 건에서 종목 매매가능 상태를 뽑는다(05-risk 5.2 검사 7).

    점상/점하는 설계상 '호가창에 상대 물량이 있는지'로 판정해야 하지만, 현재
    호가 조회(inquire-asking-price)를 연결하지 않아 현재가=상한가/하한가로 대신
    판정한다. 상한가에 물량이 남아 체결이 되는 경우까지 막으므로 보수적으로 과하다.
    """
    out = quote.get("output") if isinstance(quote.get("output"), dict) else quote
    out = out or {}
    price = _num(out.get("stck_prpr"))
    upper, lower = _num(out.get("stck_mxpr")), _num(out.get("stck_llam"))
    stat = str(out.get("iscd_stat_cls_code") or "").strip()
    return StockStatus(
        limit_up=price is not None and upper is not None and price >= upper,
        limit_down=price is not None and lower is not None and price <= lower,
        suspended=stat in _STAT_SUSPENDED or _is_on(out.get("temp_stop_yn")),
        vi=_is_on(out.get("vi_cls_code")),
        overheated=stat in _STAT_OVERHEATED,
    )


@dataclass
class Quote:
    """사이클 시점 종목 스냅샷 — 현재가와 매매가능 상태를 한 번의 조회로 함께 받는다."""
    last_price: float | None
    status: StockStatus


def quote_of(payload: dict[str, Any]) -> Quote:
    """KIS 현재가 응답 한 건을 Quote로 정규화한다."""
    out = payload.get("output") if isinstance(payload.get("output"), dict) else payload
    price = _num((out or {}).get("stck_prpr"))
    return Quote(price if price and price > 0 else None, stock_status(payload))


def fetch_quotes(client: Any, codes: list[str]) -> dict[str, Quote]:
    """워치리스트 종목의 현재가·상태를 조회한다(조회 실패는 맵에서 뺀다).

    조회에 실패한 종목은 맵에 없다 — 상태를 모르는 종목을 정상으로 넣으면 거래정지
    종목에 진입할 수 있으므로, 호출 측이 '모름'을 진입 불가로 다룬다.
    현재가는 설계 4.2 3단계의 용도(진입가·손절가 확정)로만 쓰고, 지표는 전일 확정
    일봉으로 계산한다 — 장중 미완성 봉으로 ATR·점수를 내면 안 된다.
    """
    out: dict[str, Quote] = {}
    for code in codes:
        try:
            out[code] = quote_of(client.get_price(code))
        except Exception as e:                      # 한 종목 실패가 사이클을 멈추지 않는다
            log.warning("현재가 조회 실패 %s: %s", code, type(e).__name__)
    return out


def reconcile_balance(conn, kis_holdings: dict[str, int]) -> tuple[bool, str]:
    """KIS 실잔고와 DB `Positions`(open)를 대조한다. 반환: (일치 여부, 사유).

    05-risk 5.2 검사 1(선행 게이트). 불일치는 사이클 중단 사유다 — 우리가 아는
    보유와 실제 보유가 다르면 손절 수량·청산 판단이 전부 틀리기 때문이다.
    """
    rows = conn.execute(
        'SELECT symbol_id, quantity FROM positions '
        "WHERE status = 'open' AND quantity > 0"
    ).fetchall()
    book = {r["symbol_id"]: int(r["quantity"]) for r in rows}
    live = {c: int(q) for c, q in kis_holdings.items() if q > 0}
    if book == live:
        return True, ""
    diffs = [
        f"{code} 장부{book.get(code, 0)}≠실잔고{live.get(code, 0)}"
        for code in sorted(set(book) | set(live))
        if book.get(code, 0) != live.get(code, 0)
    ]
    return False, "잔고 불일치: " + ", ".join(diffs[:10])


def build_market_state(
    conn,
    *,
    kis_holdings: dict[str, int] | None = None,
    trade_date: date | None = None,
    client: Any = None,
    check_data: bool = True,
) -> tuple[MarketState, str]:
    """사이클 게이트에 넣을 시장·시스템 상태를 모은다. 반환: (상태, 사람이 읽을 사유).

    사이드카·KRX 서킷브레이커는 조회 경로가 없어 항상 False다 — 지금은 이 둘을
    감지하지 못한다는 뜻이며, 있는 척하지 않기 위해 여기에 적어 둔다.
    """
    notes: list[str] = []

    balance_ok = True
    if kis_holdings is not None:
        balance_ok, why = reconcile_balance(conn, kis_holdings)
        if not balance_ok:
            notes.append(why)

    day = trade_date or None
    open_market = is_trading_day(day, client=client)
    if not open_market:
        notes.append(f"{day or '오늘'}은 거래일이 아니다")

    prices_ok = True
    if check_data:
        prices_ok, why = corrections.check_freshness(
            conn, trade_date=trade_date or kst_today()
        )
        if not prices_ok:
            notes.append(why)

    return (
        MarketState(
            balance_ok=balance_ok,
            halted=not open_market,
            sidecar=False,          # 조회 경로 없음 — 감지하지 못한다
            market_cb=False,        # 〃
            prices_ok=prices_ok,
        ),
        "; ".join(notes),
    )
