"""운영 Tier0 시세·수급 수집 (data/market_data, 03-arch 3.1 2단계·04-data 0b).

사이클이 *결정 시점에* 필요한 **최근 구간**의 시세·수급을 KIS에서 받아 `panel.build_panel`·
`engine`이 먹는 prices dict(code→date인덱스 OHLCV[+inst_net·foreign_net])로 돌려준다.

백테스트 수집(`data.collect`→parquet 캐시)과 다른 레이어다:
- 백테스트: 5~10년치를 1회 받아 *폐기 가능 캐시*에 적재(실전은 안 읽음 — data/cache 주석).
- 운영: 매 사이클 *최근 구간만* 받아 **메모리로** 넘긴다(디스크 영속 없음 → vintage 오염·
  실전/백테스트 캐시 혼선 방지). 같은 정규화·피처 정의를 재사용해 둘이 어긋나지 않게.

OHLCV는 KIS 일봉(확정 스키마, `kis_history` 재사용)이 정본이다. 수급(투자자 순매수)은 KIS
`inquire-investor`가 **최근 30거래일만** 주고(external-apis §3 실측) 출력 컬럼이 아직
*라이브 미검증*이라, 컬럼 가드로 다르면 명확히 실패시키고(조용한 오염 금지) 수급 실패는
OHLCV를 막지 않는다(수급 없으면 스크리너가 중립 처리).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from broker.kis_client import KISClient
from data.sources import kis_history
from data.sources.kis_history import KISHistoryError

# KIS inquire-investor(FHKST01010900) 출력 컬럼 → 표준. ★라이브 검증 필요(external-apis §3).
_INVESTOR_COLS = {
    "stck_bsop_date": "date",
    "orgn_ntby_qty": "inst_net",     # 기관계 순매수 수량
    "frgn_ntby_qty": "foreign_net",  # 외국인 순매수 수량
}


def _normalize_investor(rows: list[dict]) -> pd.DataFrame:
    """투자자 순매수 원시행 → date·inst_net·foreign_net. 컬럼 다르면 KISHistoryError."""
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
    """한 종목 최근 OHLCV(date 인덱스). lookback_days는 달력일(피처 워밍업 여유 포함)."""
    end = end or date.today()
    start = end - timedelta(days=lookback_days)
    df = kis_history.fetch_ohlcv_range(
        client, code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    )
    return df.set_index("date").sort_index() if not df.empty else df


def fetch_supply(client: KISClient, code: str) -> pd.DataFrame:
    """한 종목 최근(≤30거래일) 투자자 순매수(date 인덱스 inst_net·foreign_net)."""
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
    """여러 종목 운영 시세(+수급) → (prices, failed). 시세 조회만 하므로 mode 기본 real.

    한 종목 OHLCV 실패는 격리(failed에 모음). 수급 실패는 그 종목 OHLCV는 살리고 수급만 뺀다
    (수급 없는 종목은 패널에서 supply 결측 → 스크리너 중립 처리).
    """
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
