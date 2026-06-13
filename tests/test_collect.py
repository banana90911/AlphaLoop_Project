"""수집 오케스트레이션 — 이어받기(skip)·격리 (네트워크 없이, mock)."""
import pandas as pd

import data.collect as collect
from data import cache
from data.sources import kis_history, naver_finance


def _patch_fetchers(monkeypatch, counter: list):
    def fake(*a, **k):
        counter.append(1)
        return pd.DataFrame({"date": [], "close": []})
    monkeypatch.setattr(kis_history, "fetch_ohlcv_range", fake)
    monkeypatch.setattr(kis_history, "fetch_short_sale_range", fake)
    monkeypatch.setattr(naver_finance, "fetch_supply", fake)


def test_collect_one_fetches_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    calls: list = []
    _patch_fetchers(monkeypatch, calls)
    r = collect.collect_one(None, None, "005930", "20260401", "20260612")
    assert len(calls) == 3                       # 3종 다 수집
    assert set(r) == {"ohlcv", "short", "supply"}


def test_collect_one_skips_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    calls: list = []
    _patch_fetchers(monkeypatch, calls)
    collect.collect_one(None, None, "005930", "20260401", "20260612")   # 1차: 3회
    r2 = collect.collect_one(None, None, "005930", "20260401", "20260612")  # 2차: 전부 캐시
    assert all(v == "skip" for v in r2.values())
    assert len(calls) == 3                       # 추가 호출 없음


def test_collect_one_force_refetches(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    calls: list = []
    _patch_fetchers(monkeypatch, calls)
    collect.collect_one(None, None, "005930", "20260401", "20260612")
    collect.collect_one(None, None, "005930", "20260401", "20260612", force=True)
    assert len(calls) == 6                       # force면 캐시 무시 재수집
