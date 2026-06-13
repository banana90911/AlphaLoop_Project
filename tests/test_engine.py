"""백테스트 엔진 통합 — 합성 데이터로 진입·청산·equity (backtest/engine)."""
from datetime import date, timedelta

import pandas as pd

from backtest import engine


def _series(n: int, daily: float, base: float = 100.0) -> list[float]:
    return [base * (1 + daily) ** i for i in range(n)]


def _df(n: int, daily: float) -> pd.DataFrame:
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    close = _series(n, daily)
    return pd.DataFrame(
        {
            "open": close,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "close": close,
            "volume": [2_000_000] * n,
        },
        index=dates,
    )


def test_build_features_columns():
    f = engine.build_features(_df(100, 0.003))
    for col in ("close", "momentum", "atr", "lowvol", "alignment", "value_traded"):
        assert col in f.columns


def test_uptrend_enters_and_profits():
    prices = {"UP": _df(150, 0.005), "DOWN": _df(150, -0.003)}
    markets = {"UP": "KOSPI", "DOWN": "KOSDAQ"}
    dates = prices["UP"].index
    r = engine.run(prices, markets, start=dates[0], end=dates[-1],
                   initial_capital=10_000_000)
    assert not r.equity.empty
    # 우상향 종목 진입 → 익절(부분청산) 거래 발생, 수익
    assert len(r.trades) > 0
    assert any(t.code == "UP" and t.net_pnl > 0 for t in r.trades)
    assert r.total_return() > 0


def test_no_trades_before_warmup():
    # 워밍업(60) 못 채우는 짧은 데이터 → 거래 없음
    prices = {"A": _df(40, 0.005), "B": _df(40, -0.003)}
    markets = {"A": "KOSPI", "B": "KOSPI"}
    dates = prices["A"].index
    r = engine.run(prices, markets, start=dates[0], end=dates[-1],
                   initial_capital=10_000_000)
    assert len(r.trades) == 0
    # 거래 없으니 자본 보존
    assert r.equity.iloc[-1] == 10_000_000


def test_downtrend_only_avoids_entry():
    # 전부 하락 추세 → 스크리너 점수 낮아 진입 회피, 자본 보존
    prices = {"X": _df(150, -0.004), "Y": _df(150, -0.006)}
    markets = {"X": "KOSPI", "Y": "KOSDAQ"}
    dates = prices["X"].index
    r = engine.run(prices, markets, start=dates[0], end=dates[-1],
                   initial_capital=10_000_000)
    assert r.equity.iloc[-1] == 10_000_000
