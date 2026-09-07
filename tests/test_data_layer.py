"""
description:        데이터 레이어 — KIS 수집 정규화·구간분할 + parquet 캐시 (네트워크 없이).
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date

import pandas as pd
import pytest

from data import cache
from data.sources import kis_history as kh


def test_windows_cover_range_descending():
    wins = list(kh._windows(date(2025, 1, 1), date(2025, 12, 31), 100))
    # 최근→과거 순, 전 구간 커버, 겹침 없음
    assert wins[0][1] == date(2025, 12, 31)
    assert wins[-1][0] == date(2025, 1, 1)
    for (s, e) in wins:
        assert s <= e
    for earlier, later in zip(wins[1:], wins[:-1], strict=True):
        assert earlier[1] < later[0]  # 겹치지 않음


def test_normalize_ohlcv_maps_and_filters():
    rows = [
        {"stck_bsop_date": "20260102", "stck_oprc": "100", "stck_hgpr": "110",
         "stck_lwpr": "90", "stck_clpr": "105", "acml_vol": "1000"},
        {"stck_bsop_date": "20251231", "stck_oprc": "95", "stck_hgpr": "99",
         "stck_lwpr": "90", "stck_clpr": "98", "acml_vol": "800"},  # 범위 밖
    ]
    df = kh._normalize(rows, kh._OHLCV_COLS, date(2026, 1, 1), date(2026, 1, 31))
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(df) == 1
    assert df.iloc[0]["date"] == date(2026, 1, 2)
    assert df.iloc[0]["close"] == 105


def test_normalize_missing_column_raises():
    with pytest.raises(kh.KISHistoryError):
        kh._normalize([{"stck_bsop_date": "20260102"}], kh._OHLCV_COLS,
                      date(2026, 1, 1), date(2026, 1, 31))


def test_normalize_empty_returns_typed_frame():
    df = kh._normalize([], kh._SHORT_COLS, date(2026, 1, 1), date(2026, 1, 31))
    assert df.empty
    assert list(df.columns) == ["date", "close", "short_qty", "short_ratio"]


def test_cache_roundtrip_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    df = pd.DataFrame({"date": [date(2026, 1, 2)], "close": [105]})
    assert not cache.exists("ohlcv_X")
    cache.save("ohlcv_X", df)
    assert cache.exists("ohlcv_X")
    assert len(cache.load("ohlcv_X")) == 1
    assert cache.load("missing") is None
    assert cache.clear() == 1
    assert not cache.exists("ohlcv_X")


# ── 지수 수신 — yfinance 컬럼 대소문자 (2026-09-07 배치 실패 회귀) ──────────
def _fake_yf_frame(*, upper: bool, multi: bool) -> pd.DataFrame:
    """yfinance가 돌려주는 모양을 흉내낸다."""
    cols = ["Open", "High", "Low", "Close", "Volume"] if upper else \
           ["open", "high", "low", "close", "volume"]
    df = pd.DataFrame(
        [[1.0, 2.0, 0.5, 1.5, 100], [1.5, 2.5, 1.0, 2.0, 200]],
        index=pd.to_datetime(["2026-09-03", "2026-09-04"]), columns=cols,
    )
    if multi:                                  # 단일 심볼도 (필드, 심볼) 튜플로 온다
        df.columns = pd.MultiIndex.from_tuples([(c, "^KS11") for c in cols])
    return df


@pytest.mark.parametrize("upper", [True, False])
@pytest.mark.parametrize("multi", [True, False])
def test_fetch_index_accepts_either_column_case(monkeypatch, upper, multi):
    """yfinance가 'Close'로 주든 'close'로 주든 받아낸다.

    실제로 yfinance는 첫 글자를 대문자로 준다. 예전 코드가 소문자 이름으로만
    rename을 걸어서 2026-09-07 배치의 지수 적재가 통째로 실패했다.
    """
    from data.sources import index_history as ih

    monkeypatch.setattr(ih.yf, "download",
                        lambda *a, **k: _fake_yf_frame(upper=upper, multi=multi))
    df = ih.fetch_index("KOSPI", "20260901", "20260905")
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [1.5, 2.0]
    assert df["date"].iloc[0] == date(2026, 9, 3)


def test_fetch_index_reports_which_columns_are_missing(monkeypatch):
    """컬럼이 정말 없으면 무엇이 없는지 밝히고 실패한다."""
    from data.sources import index_history as ih

    bad = pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2026-09-03"]))
    monkeypatch.setattr(ih.yf, "download", lambda *a, **k: bad)
    with pytest.raises(ih.IndexHistoryError) as e:
        ih.fetch_index("KOSPI", "20260901", "20260905")
    assert "open" in str(e.value)              # 없는 컬럼을 알려준다
