"""데이터 레이어 — KIS 수집 정규화·구간분할 + parquet 캐시 (네트워크 없이)."""
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
