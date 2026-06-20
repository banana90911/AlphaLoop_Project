"""운영 시세·수급 수집 — 정규화·실패격리·수급결측 (data/market_data)."""
from datetime import date

import pandas as pd
import pytest

from data import market_data
from data.sources.kis_history import KISHistoryError


class _StubClient:
    """KIS 조회만 흉내내는 스텁(네트워크 없음). 종목별 응답을 주입."""

    def __init__(self, charts=None, investors=None, fail=()):
        self._charts = charts or {}
        self._investors = investors or {}
        self._fail = set(fail)

    def get_daily_chart(self, code, start, end, *, adjusted=True):
        if code in self._fail:
            raise RuntimeError("일봉 조회 실패")
        return self._charts.get(code, [])

    def get_investor(self, code):
        return self._investors.get(code, [])


def _chart_rows(n=5, base=1000, end=date(2024, 6, 14)):
    # KIS 일봉 원시행(stck_* 컬럼) — kis_history._normalize가 먹는 형식
    dates = pd.bdate_range(end=end, periods=n).strftime("%Y%m%d")
    rows = []
    for i, d in enumerate(dates):
        px = base + i * 10
        rows.append({
            "stck_bsop_date": d, "stck_oprc": px, "stck_hgpr": px + 5,
            "stck_lwpr": px - 5, "stck_clpr": px, "acml_vol": 100000,
        })
    return rows


def _investor_rows():
    return [
        {"stck_bsop_date": "20240610", "orgn_ntby_qty": "1000", "frgn_ntby_qty": "2000"},
        {"stck_bsop_date": "20240611", "orgn_ntby_qty": "-500", "frgn_ntby_qty": "300"},
    ]


def test_fetch_ohlcv_indexed_by_date():
    client = _StubClient(charts={"A": _chart_rows()})
    df = market_data.fetch_ohlcv(client, "A", end=date(2024, 6, 20))
    assert df.index.name == "date"
    assert list(df.columns[:5]) == ["open", "high", "low", "close", "volume"]
    assert df["close"].iloc[-1] == 1040


def test_normalize_investor_guards_unknown_columns():
    with pytest.raises(KISHistoryError):
        market_data._normalize_investor([{"wrong_col": "1"}])


def test_fetch_supply_parses_net_flows():
    client = _StubClient(investors={"A": _investor_rows()})
    s = market_data.fetch_supply(client, "A")
    assert s.loc[date(2024, 6, 10), "foreign_net"] == 2000
    assert s.loc[date(2024, 6, 11), "inst_net"] == -500


def test_fetch_prices_joins_supply():
    client = _StubClient(charts={"A": _chart_rows()}, investors={"A": _investor_rows()})
    prices, failed = market_data.fetch_prices(["A"], client=client, end=date(2024, 6, 20))
    assert not failed
    assert "foreign_net" in prices["A"].columns


def test_fetch_prices_isolates_ohlcv_failure():
    client = _StubClient(charts={"OK": _chart_rows()}, fail=["BAD"])
    prices, failed = market_data.fetch_prices(["OK", "BAD"], client=client, end=date(2024, 6, 20))
    assert "OK" in prices and "BAD" not in prices
    assert failed and failed[0][0] == "BAD"


def test_supply_failure_keeps_ohlcv():
    # 수급 컬럼이 깨져도 OHLCV는 살아남고 supply만 빠진다
    client = _StubClient(
        charts={"A": _chart_rows()}, investors={"A": [{"bad": "1"}]}
    )
    prices, failed = market_data.fetch_prices(["A"], client=client, end=date(2024, 6, 20))
    assert "A" in prices
    assert "foreign_net" not in prices["A"].columns


def test_with_supply_false_skips_investor():
    client = _StubClient(charts={"A": _chart_rows()})  # investors 미주입
    prices, _ = market_data.fetch_prices(
        ["A"], client=client, with_supply=False, end=date(2024, 6, 20)
    )
    assert "foreign_net" not in prices["A"].columns


def test_fetched_prices_flow_into_panel():
    # 운영 fetch 출력이 build_panel 계약(컬럼명)과 맞물리는지 — 워밍업 충분한 70행
    from data import panel
    client = _StubClient(
        charts={"A": _chart_rows(n=70), "B": _chart_rows(n=70, base=2000)}
    )
    prices, _ = market_data.fetch_prices(
        ["A", "B"], client=client, with_supply=False, end=date(2024, 9, 30)
    )
    pnl = panel.build_panel(prices)
    assert set(pnl.index) == {"A", "B"}
    assert "momentum" in pnl.columns
