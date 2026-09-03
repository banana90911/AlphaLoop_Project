"""
description:        운영 Tier0 시세·수급 수집 (사이클용 최근 구간 조회)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date, datetime, timedelta

import pandas as pd

from broker.kis_client import KISClient
from data.sources import kis_history
from data.sources.kis_history import KISHistoryError

# KIS inquire-investor(FHKST01010900) 출력 컬럼 → 표준
_INVESTOR_COLS = {
    "stck_bsop_date": "date",
    "orgn_ntby_qty": "inst_net",     # 기관계 순매수 수량
    "frgn_ntby_qty": "foreign_net",  # 외국인 순매수 수량
}


def _normalize_investor(rows: list[dict]) -> pd.DataFrame:
    """투자자 순매수 원시행을 date·inst_net·foreign_net 표준 컬럼으로 바꾼다."""
    std = list(_INVESTOR_COLS.values())
    if not rows:
        return pd.DataFrame(columns=std)
    df = pd.DataFrame(rows)
    missing = set(_INVESTOR_COLS) - set(df.columns)
    if missing:
        raise KISHistoryError(f"KIS investor 컬럼 누락 {missing} — 명세 변경/미검증 의심")
    df = df[list(_INVESTOR_COLS)].rename(columns=_INVESTOR_COLS)
    df["date"] = df["date"].map(lambda s: datetime.strptime(s, "%Y%m%d").date())
    for c in ("inst_net", "foreign_net"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["inst_net", "foreign_net"]).drop_duplicates("date")


def fetch_ohlcv(
    client: KISClient, code: str, *, lookback_days: int = 200, end: date | None = None
) -> pd.DataFrame:
    """한 종목 최근 OHLCV(date 인덱스)를 조회한다. lookback_days는 달력일."""
    end = end or date.today()
    start = end - timedelta(days=lookback_days)
    df = kis_history.fetch_ohlcv_range(
        client, code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    )
    return df.set_index("date").sort_index() if not df.empty else df


def fetch_supply(client: KISClient, code: str) -> pd.DataFrame:
    """한 종목 최근(≤30거래일) 투자자 순매수를 조회한다."""
    df = _normalize_investor(client.get_investor(code))
    return df.set_index("date").sort_index() if not df.empty else df


def fetch_prices(
    codes: list[str],
    *,
    mode: str = "real",
    lookback_days: int = 200,
    with_supply: bool = True,
    end: date | None = None,
    client: KISClient | None = None,
) -> tuple[dict[str, pd.DataFrame], list[tuple[str, str]]]:
    """여러 종목의 운영 시세(+수급)를 조회한다. 반환: (prices, failed)."""
    client = client or KISClient(mode=mode)
    prices: dict[str, pd.DataFrame] = {}
    failed: list[tuple[str, str]] = []
    for code in codes:
        try:
            df = fetch_ohlcv(client, code, lookback_days=lookback_days, end=end)
        except Exception as e:  # 한 종목 실패가 배치를 멈추지 않음
            failed.append((code, f"{type(e).__name__}: {e}"))
            continue
        if df.empty:
            continue
        if with_supply:
            try:
                s = fetch_supply(client, code)
                if not s.empty:
                    df = df.join(s[["inst_net", "foreign_net"]], how="left")
            except Exception:  # 수급 실패는 OHLCV를 막지 않음(스크리너 중립)
                pass
        prices[code] = df
    return prices, failed
