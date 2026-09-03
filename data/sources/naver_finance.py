"""
description:        네이버 금융 스크래핑 (백테스트용 과거 시세·수급)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import time
from datetime import date, datetime
from io import StringIO

import pandas as pd
import requests

_BASE = "https://finance.naver.com/item"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_PAUSE = 0.4          # 요청 간 지연(매너·차단 회피)
_MAX_PAGES = 600      # 안전 상한(무한 루프 방지)

# 네이버 원본 컬럼 → 표준 컬럼. 구조 검증의 기준이기도 하다.
_OHLCV_MAP = {"날짜": "date", "시가": "open", "고가": "high", "저가": "low",
              "종가": "close", "거래량": "volume"}
_SUPPLY_MAP = {"날짜": "date", "종가": "close", "거래량": "volume",
               "기관": "inst_net", "외국인": "foreign_net"}


class NaverParseError(RuntimeError):
    """네이버 페이지 구조가 기대와 다름(스크래퍼 노후 신호)."""


def _new_session() -> requests.Session:
    """네이버 요청용 세션(공통 헤더 포함)을 만든다."""
    s = requests.Session()
    s.headers.update(_HEADERS)
    return s


def _get_tables(session: requests.Session, path: str, code: str, page: int) -> list[pd.DataFrame]:
    """한 페이지의 HTML 표들을 파싱해 반환한다."""
    r = session.get(f"{_BASE}/{path}?code={code}&page={page}", timeout=10)
    r.raise_for_status()
    r.encoding = "euc-kr"
    return pd.read_html(StringIO(r.text))


def _to_date(s: str) -> date:
    """'YYYY.MM.DD' 문자열을 date로 변환한다."""
    return datetime.strptime(s.strip(), "%Y.%m.%d").date()


def _parse_date_str(d: str) -> date:
    """'YYYYMMDD' 문자열을 date로 변환한다."""
    return datetime.strptime(d, "%Y%m%d").date()


def _paginate(
    session: requests.Session,
    path: str,
    code: str,
    start: date,
    end: date,
    parse_page,
) -> pd.DataFrame:
    """start~end까지 페이지를 거슬러 수집한다(마지막 페이지 반복 시 중단)."""
    frames: list[pd.DataFrame] = []
    seen_oldest: date | None = None
    for page in range(1, _MAX_PAGES + 1):
        df = parse_page(_get_tables(session, path, code, page))
        if df.empty:
            break
        oldest = df["date"].min()
        if oldest == seen_oldest:
            break
        seen_oldest = oldest
        frames.append(df)
        if oldest <= start:
            break
        time.sleep(_PAUSE)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).drop_duplicates("date")
    out = out[(out["date"] >= start) & (out["date"] <= end)]
    return out.sort_values("date").reset_index(drop=True)


def _pick_table(tables: list[pd.DataFrame], required_ko: set[str]) -> pd.DataFrame:
    """필요한 한글 컬럼을 모두 가진 표를 고른다(없으면 에러)."""
    for t in tables:
        cols = {str(c[0]) if isinstance(c, tuple) else str(c) for c in t.columns}
        if required_ko <= cols:
            return t
    raise NaverParseError(f"기대 컬럼 {required_ko} 가진 표 없음 — 네이버 구조 변경 의심")


def _parse_ohlcv(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """시세 표를 표준 OHLCV 컬럼으로 변환한다."""
    t = _pick_table(tables, set(_OHLCV_MAP)).dropna(how="all")
    t = t.rename(columns=_OHLCV_MAP)[list(_OHLCV_MAP.values())].dropna(subset=["date"])
    t["date"] = t["date"].map(_to_date)
    for c in ("open", "high", "low", "close", "volume"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    return t.dropna()


def _parse_supply(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """수급 표(2단 멀티헤더)를 표준 컬럼으로 변환한다."""
    # frgn은 2단 멀티헤더 → 상위 레벨(날짜/종가/거래량/기관/외국인)로 식별
    for t in tables:
        top = {str(c[0]) if isinstance(c, tuple) else str(c) for c in t.columns}
        if not ({"날짜", "기관", "외국인"} <= top):
            continue
        df = t.copy()
        df.columns = [str(c[0]) if isinstance(c, tuple) else str(c) for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]       # 상위 중복('종가'·'거래량') 첫 것만
        df = df.rename(columns=_SUPPLY_MAP)
        keep = [c for c in _SUPPLY_MAP.values() if c in df]
        df = df[keep].dropna(subset=["date"])
        df["date"] = df["date"].map(_to_date)
        for c in ("close", "volume", "inst_net", "foreign_net"):
            if c in df:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["inst_net", "foreign_net"])
    raise NaverParseError("수급(frgn) 표 구조 변경 의심 — 날짜/기관/외국인 헤더 없음")


def fetch_ohlcv(code: str, start: str, end: str, *, session: requests.Session | None = None
                ) -> pd.DataFrame:
    """일별 시세(상폐 포함)를 조회한다. start/end='YYYYMMDD'."""
    s = session or _new_session()
    return _paginate(s, "sise_day.naver", code, _parse_date_str(start), _parse_date_str(end),
                     _parse_ohlcv)


def fetch_supply(code: str, start: str, end: str, *, session: requests.Session | None = None
                 ) -> pd.DataFrame:
    """투자자 수급(외국인·기관 순매매)을 조회한다. start/end='YYYYMMDD'."""
    s = session or _new_session()
    return _paginate(s, "frgn.naver", code, _parse_date_str(start), _parse_date_str(end),
                     _parse_supply)
