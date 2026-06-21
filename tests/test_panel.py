"""운영 횡단면 패널 — asof 최신행·워밍업·결측 처리 (data/panel)."""
from datetime import date

import numpy as np
import pandas as pd

from data import panel


def _series(n: int, start: float, step: float, end: date = date(2024, 6, 28)) -> pd.DataFrame:
    """n거래일 OHLCV. close가 step씩 변하는 단순 시계열."""
    idx = pd.bdate_range(end=end, periods=n).date
    close = start + step * np.arange(n)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1_000_000.0},
        index=pd.Index(idx, name="date"),
    )


def test_panel_has_screen_columns():
    prices = {"A": _series(120, 1000, 10), "B": _series(120, 2000, -5)}
    pnl = panel.build_panel(prices)
    assert set(pnl.index) == {"A", "B"}
    for col in ("momentum", "lowvol", "alignment", "value_traded"):
        assert col in pnl.columns


def test_rising_stock_has_positive_momentum():
    # 12-1 모멘텀 워밍업(252+20) 이상 길이라야 momentum 값이 산출됨
    prices = {"UP": _series(300, 1000, 10), "DOWN": _series(300, 2000, -5)}
    pnl = panel.build_panel(prices)
    assert pnl.loc["UP", "momentum"] > 0
    assert pnl.loc["DOWN", "momentum"] < 0


def test_warmup_incomplete_drops_stock():
    # 12-1 모멘텀 워밍업 미완(30행) → momentum NaN이지만 close는 유효해 행은 남고
    # momentum만 NaN(스크리너가 중립 처리). 행 자체가 빠지는 건 close가 없을 때다.
    prices = {"SHORT": _series(30, 1000, 10)}
    pnl = panel.build_panel(prices)
    assert "SHORT" in pnl.index
    assert pd.isna(pnl.loc["SHORT", "momentum"])


def test_empty_and_none_series_skipped():
    prices = {"EMPTY": pd.DataFrame(), "NONE": None, "OK": _series(120, 1000, 10)}
    pnl = panel.build_panel(prices)
    assert list(pnl.index) == ["OK"]


def test_asof_takes_latest_row_at_or_before():
    df = _series(120, 1000, 10, end=date(2024, 6, 28))
    asof = df.index[80]                       # 중간 거래일
    pnl = panel.build_panel({"A": df}, asof=asof)
    # asof 시점 close = 1000 + 10*80
    assert pnl.loc["A", "value_traded"] == (1000 + 10 * 80) * 1_000_000.0


def test_supply_column_present_when_flow_given():
    df = _series(120, 1000, 10)
    df["foreign_net"] = 5000.0
    df["inst_net"] = 3000.0
    pnl = panel.build_panel({"A": df})
    assert "supply" in pnl.columns
    assert pnl.loc["A", "supply"] > 0
