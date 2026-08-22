"""백테스트 입력 로더 — 캐시 parquet → 엔진용 prices dict (네트워크 없이)."""
import pandas as pd

from backtest import loader
from data import cache


def _ohlcv(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"date": pd.to_datetime(dates).date, "open": 1.0, "high": 2.0,
         "low": 0.5, "close": 1.5, "volume": 100}
    )


def _supply(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"date": pd.to_datetime(dates).date,
         "inst_net": [10, 20], "foreign_net": [-5, 5]}
    )


def test_load_one_none_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    assert loader.load_one("005930") is None


def test_load_one_sets_sorted_date_index(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.save("ohlcv_005930", _ohlcv(["20260102", "20260101"]))  # 역순 저장
    df = loader.load_one("005930")
    assert df.index.name == "date"
    assert list(df.index) == sorted(df.index)                     # 정렬됨
    assert "close" in df.columns


def test_load_one_joins_supply(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.save("ohlcv_005930", _ohlcv(["20260101", "20260102"]))
    cache.save("supply_005930", _supply(["20260101", "20260102"]))
    df = loader.load_one("005930")
    assert {"inst_net", "foreign_net"}.issubset(df.columns)
    assert df["foreign_net"].tolist() == [-5, 5]


def test_load_one_without_supply_has_ohlcv_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.save("ohlcv_005930", _ohlcv(["20260101", "20260102"]))
    df = loader.load_one("005930")
    assert "inst_net" not in df.columns


def test_load_prices_skips_missing_and_maps_markets(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.save("ohlcv_005930", _ohlcv(["20260101", "20260102"]))
    # 035720은 캐시 없음 → skip 되어야 함
    prices, markets = loader.load_prices(
        ["005930", "035720"], markets={"005930": "KOSDAQ"}
    )
    assert set(prices) == {"005930"}
    assert markets == {"005930": "KOSDAQ"}


def test_load_prices_default_market(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.save("ohlcv_005930", _ohlcv(["20260101", "20260102"]))
    prices, markets = loader.load_prices(["005930"])
    assert markets["005930"] == "KOSPI"                           # 기본값
