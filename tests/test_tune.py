"""워크포워드 파라미터 튜닝 — IS선택/OOS평가 분리·모달 추천 (backtest/tune)."""
from datetime import date, timedelta

import pandas as pd

from backtest import tune
from config.settings import load_params


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


_SMALL_GRID = {
    ("entry", "score_min"): [0.50, 0.55],
    ("entry", "stop_atr_k"): [1.5, 2.0],
}


def test_param_grid_cartesian_count():
    base = load_params("risk_params")
    cands = tune.param_grid(base, _SMALL_GRID)
    assert len(cands) == 4                      # 2 × 2
    # 원본 base 불변(deepcopy)
    assert base["entry"]["score_min"] == 0.55


def test_param_grid_overrides_only_knobs():
    base = load_params("risk_params")
    cands = tune.param_grid(base, _SMALL_GRID)
    seen = {(c["entry"]["score_min"], c["entry"]["stop_atr_k"]) for c in cands}
    assert seen == {(0.50, 1.5), (0.50, 2.0), (0.55, 1.5), (0.55, 2.0)}
    # 손잡이 외 값은 base 그대로
    assert all(c["limits"]["max_positions"] == base["limits"]["max_positions"]
               for c in cands)


def test_is_objective_no_trades_is_neg_inf():
    from backtest.engine import BacktestResult
    assert tune.is_objective(BacktestResult()) == float("-inf")


def test_walkforward_oos_after_train():
    prices = {"UP": _df(180, 0.005), "DN": _df(180, -0.003)}
    markets = {"UP": "KOSPI", "DN": "KOSDAQ"}
    base = load_params("risk_params")
    recs = tune.walkforward_tune(
        prices, markets, train_size=80, test_size=30,
        initial_capital=10_000_000, base_params=base, grid=_SMALL_GRID,
    )
    assert len(recs) >= 1
    for r in recs:
        # OOS 구간은 항상 train 미래(룩어헤드 없음)
        assert r.split.train_end < r.split.test_start
        # 고른 조합은 grid 안의 값
        assert r.best_params["entry"]["score_min"] in (0.50, 0.55)


def test_recommend_params_picks_modal():
    base = load_params("risk_params")
    sp = tune.Split(date(2024, 1, 1), date(2024, 2, 1),
                    date(2024, 2, 2), date(2024, 3, 1))

    def rec(score_min):
        p = tune.param_grid(base, _SMALL_GRID)[0]
        p["entry"]["score_min"] = score_min
        return tune.WFRecord(sp, p, 1.0, 0.0, pd.Series(dtype=float))

    # 0.50이 2표, 0.55가 1표 → 모달 0.50
    recs = [rec(0.50), rec(0.50), rec(0.55)]
    out = tune.recommend_params(recs, base, _SMALL_GRID)
    assert out["entry"]["score_min"] == 0.50


def test_recommend_params_tie_prefers_base():
    base = load_params("risk_params")          # base score_min=0.55
    sp = tune.Split(date(2024, 1, 1), date(2024, 2, 1),
                    date(2024, 2, 2), date(2024, 3, 1))

    def rec(score_min):
        p = tune.param_grid(base, _SMALL_GRID)[0]
        p["entry"]["score_min"] = score_min
        return tune.WFRecord(sp, p, 1.0, 0.0, pd.Series(dtype=float))

    # 0.50:1, 0.55:1 동률 → base(0.55) 우선
    out = tune.recommend_params([rec(0.50), rec(0.55)], base, _SMALL_GRID)
    assert out["entry"]["score_min"] == 0.55


def test_oos_equity_compounds():
    sp = tune.Split(date(2024, 1, 1), date(2024, 2, 1),
                    date(2024, 2, 2), date(2024, 3, 1))
    base = load_params("risk_params")
    p = tune.param_grid(base, _SMALL_GRID)[0]
    r1 = tune.WFRecord(sp, p, 1.0, 0.0, pd.Series([0.1, 0.0]))
    r2 = tune.WFRecord(sp, p, 1.0, 0.0, pd.Series([0.1]))
    eq = tune.oos_equity([r1, r2], initial_capital=100.0)
    # 100 → +10% → +0% → +10% = 121
    assert abs(eq.iloc[-1] - 121.0) < 1e-9


def test_perf_matrix_shape():
    prices = {"UP": _df(180, 0.005), "DN": _df(180, -0.003)}
    markets = {"UP": "KOSPI", "DN": "KOSDAQ"}
    base = load_params("risk_params")
    mat = tune.perf_matrix(
        prices, markets, train_size=80, test_size=30,
        initial_capital=10_000_000, base_params=base, grid=_SMALL_GRID,
    )
    # (n_splits, n_candidates=4)
    assert mat.ndim == 2
    assert mat.shape[1] == 4
