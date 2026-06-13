"""KIS 과거 시계열 수집 — 일봉·공매도를 구간 페이지네이션으로 5~10년치 (data 레이어).

`broker.kis_client`의 단건 조회(한 호출 최대 ~100건)를 감싸, end→start로 구간을 이동하며
누적한다(external-apis §3). 표준 컬럼으로 정규화하고, KIS 응답 컬럼이 기대와 다르면
`KISHistoryError`로 명확히 실패한다.

백테스트 입력 데이터 전용 — 저장은 `data.cache`(parquet)가 담당한다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from broker.kis_client import KISClient

_WINDOW_DAYS = 100  # 한 구간 달력일(거래일 ~70 < 100건 한도)

_OHLCV_COLS = {
    "stck_bsop_date": "date", "stck_oprc": "open", "stck_hgpr": "high",
    "stck_lwpr": "low", "stck_clpr": "close", "acml_vol": "volume",
}
_SHORT_COLS = {
    "stck_bsop_date": "date", "stck_clpr": "close",
    "ssts_cntg_qty": "short_qty", "ssts_vol_rlim": "short_ratio",
}


class KISHistoryError(RuntimeError):
    """KIS 응답 컬럼 구조가 기대와 다름."""


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _s(d: date) -> str:
    return d.strftime("%Y%m%d")


def _windows(start: date, end: date, days: int):
    """end에서 과거로 days 간격 구간 [chunk_start, chunk_end]를 순서대로 yield."""
    cur_end = end
    while cur_end >= start:
        cur_start = max(start, cur_end - timedelta(days=days - 1))
        yield cur_start, cur_end
        cur_end = cur_start - timedelta(days=1)


def _normalize(rows: list[dict], colmap: dict[str, str], start: date, end: date) -> pd.DataFrame:
    std = list(colmap.values())
    if not rows:
        return pd.DataFrame(columns=std)
    df = pd.DataFrame(rows)
    missing = set(colmap) - set(df.columns)
    if missing:
        raise KISHistoryError(f"KIS 응답 컬럼 누락 {missing} — 명세 변경 의심")
    df = df[list(colmap)].rename(columns=colmap)
    df["date"] = df["date"].map(_d)
    for c in std:
        if c != "date":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=[c for c in std if c != "date"]).drop_duplicates("date")
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    return df.sort_values("date").reset_index(drop=True)


def fetch_ohlcv_range(client: KISClient, code: str, start: str, end: str) -> pd.DataFrame:
    """일봉 5~10년. start/end='YYYYMMDD'. 컬럼 date·open·high·low·close·volume(수정주가)."""
    s, e = _d(start), _d(end)
    rows: list[dict] = []
    for ws, we in _windows(s, e, _WINDOW_DAYS):
        rows.extend(client.get_daily_chart(code, _s(ws), _s(we)))
    return _normalize(rows, _OHLCV_COLS, s, e)


def fetch_short_sale_range(client: KISClient, code: str, start: str, end: str) -> pd.DataFrame:
    """공매도 5~10년. start/end='YYYYMMDD'. 컬럼 date·close·short_qty·short_ratio."""
    s, e = _d(start), _d(end)
    rows: list[dict] = []
    for ws, we in _windows(s, e, _WINDOW_DAYS):
        rows.extend(client.get_short_sale(code, _s(ws), _s(we)))
    return _normalize(rows, _SHORT_COLS, s, e)
