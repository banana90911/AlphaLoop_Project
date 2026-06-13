"""종목 유니버스 — 마스터 파싱·보통주 필터 (네트워크 없이)."""
import pandas as pd
import pytest

from data.sources.universe import UniverseError, _parse, filter_common


def _line(code: str, name: str, group: str, tail_len: int = 10) -> str:
    head = f"{code:<9}" + "KR7000000003" + name      # [0:9]코드 [9:21]표준 [21:]종목명
    return head + group.ljust(tail_len, "0")          # 끝 고정폭: [0:2]=그룹코드


def test_parse_extracts_fields():
    df = _parse([_line("005930", "삼성전자", "ST")], "KOSPI", 10)
    row = df.iloc[0]
    assert row["code"] == "005930"
    assert row["name"] == "삼성전자"
    assert row["group"] == "ST"
    assert row["market"] == "KOSPI"


def test_parse_skips_short_lines():
    df = _parse([_line("005930", "삼성", "ST"), "짧음"], "KOSPI", 10)
    assert len(df) == 1


def test_parse_empty_raises():
    with pytest.raises(UniverseError):
        _parse(["짧"], "KOSPI", 10)


def test_filter_common_keeps_common_stock():
    df = pd.DataFrame([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "group": "ST"},
        {"code": "005935", "name": "삼성전자우", "market": "KOSPI", "group": "ST"},  # 우선주
        {"code": "069500", "name": "KODEX 200", "market": "KOSPI", "group": "EF"},  # ETF
        {"code": "123450", "name": "엔에이치스팩", "market": "KOSDAQ", "group": "ST"},  # 스팩
    ])
    out = filter_common(df)
    assert list(out["code"]) == ["005930"]
