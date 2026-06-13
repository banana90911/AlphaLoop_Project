"""성과 지표·벤치마크 (eval/metrics, 10-1)."""
import pandas as pd

from eval import metrics


def test_total_return():
    eq = pd.Series([100, 110, 120])
    assert abs(metrics.total_return(eq) - 0.2) < 1e-9


def test_cagr_one_year():
    # 252거래일 후 2배 → CAGR ~100%
    eq = pd.Series([100.0] + [None] * 250 + [200.0]).interpolate()
    assert abs(metrics.cagr(eq, ppy=252) - 1.0) < 0.05


def test_sharpe_zero_when_flat():
    eq = pd.Series([100.0] * 10)
    assert metrics.sharpe(metrics.daily_returns(eq)) == 0.0


def test_sharpe_positive_for_steady_gains():
    eq = pd.Series([100 * 1.001 ** i for i in range(100)])
    assert metrics.sharpe(metrics.daily_returns(eq)) > 0


def test_max_drawdown():
    # 100→120→90 : peak 120 대비 -25%
    eq = pd.Series([100, 120, 90, 110])
    assert abs(metrics.max_drawdown(eq) - (-0.25)) < 1e-9


def test_max_drawdown_zero_when_monotonic():
    eq = pd.Series([100, 110, 120])
    assert metrics.max_drawdown(eq) == 0.0


def test_calmar_sign():
    eq = pd.Series([100 * 1.001 ** i for i in range(300)])
    # 단조 상승이면 MDD=0 → calmar 0(정의상)
    assert metrics.calmar(eq) == 0.0


def test_summary_keys():
    eq = pd.Series([100 * 1.001 ** i for i in range(100)])
    s = metrics.summary(eq)
    assert set(s) == {"total_return", "cagr", "sharpe", "sortino",
                      "max_drawdown", "calmar"}


def test_buy_and_hold_normalized():
    price = pd.Series([50, 55, 60])
    eq = metrics.buy_and_hold_equity(price, capital=1000)
    assert eq.iloc[0] == 1000
    assert abs(eq.iloc[-1] - 1200) < 1e-9   # 60/50*1000


def test_cash_equity_flat():
    idx = pd.RangeIndex(5)
    eq = metrics.cash_equity(idx, capital=1000)
    assert (eq == 1000).all()


def test_equal_weight_average():
    prices = {"A": pd.Series([100.0, 200.0]), "B": pd.Series([100.0, 100.0])}
    eq = metrics.equal_weight_equity(prices, capital=1.0)
    # A 2배, B 1배 → 평균 1.5
    assert abs(eq.iloc[-1] - 1.5) < 1e-9
