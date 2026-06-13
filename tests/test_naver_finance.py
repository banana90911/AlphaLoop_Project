"""네이버 스크래퍼 파싱 로직 (네트워크 없이, 파서 격리 검증)."""
from datetime import date

import pandas as pd
import pytest

from data.sources.naver_finance import (
    NaverParseError,
    _parse_ohlcv,
    _parse_supply,
    _to_date,
)


def test_to_date():
    assert _to_date(" 2026.06.12 ") == date(2026, 6, 12)


def test_parse_ohlcv_ok():
    t = pd.DataFrame({
        "날짜": ["2026.06.12"], "종가": [322500], "전일비": ["상승"],
        "시가": [326000], "고가": [339000], "저가": [320000], "거래량": [30721836],
    })
    df = _parse_ohlcv([t])
    row = df.iloc[0]
    assert row["date"] == date(2026, 6, 12)
    assert row["close"] == 322500
    assert row["high"] == 339000


def test_parse_ohlcv_drops_blank_rows():
    t = pd.DataFrame({
        "날짜": [None, "2026.06.11"], "종가": [None, 299000], "시가": [None, 290500],
        "고가": [None, 306500], "저가": [None, 287500], "거래량": [None, 31420307],
    })
    assert len(_parse_ohlcv([t])) == 1


def test_parse_ohlcv_bad_structure_raises():
    with pytest.raises(NaverParseError):
        _parse_ohlcv([pd.DataFrame({"엉뚱한컬럼": [1]})])


def test_parse_supply_ok():
    # frgn 2단 멀티헤더 모사
    cols = pd.MultiIndex.from_tuples([
        ("날짜", "날짜"), ("종가", "종가"), ("거래량", "거래량"),
        ("기관", "순매매량"), ("외국인", "순매매량"),
    ])
    t = pd.DataFrame([["2026.06.11", 299000, 31420307, -5437840, 549645]], columns=cols)
    df = _parse_supply([t])
    row = df.iloc[0]
    assert row["inst_net"] == -5437840
    assert row["foreign_net"] == 549645


def test_parse_supply_bad_structure_raises():
    cols = pd.MultiIndex.from_tuples([("날짜", "날짜"), ("종가", "종가")])
    with pytest.raises(NaverParseError):
        _parse_supply([pd.DataFrame([["2026.06.11", 1]], columns=cols)])
