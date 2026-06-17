"""워크포워드 파라미터 튜닝 (backtest/tune, 10-1).

과최적화 방지의 본체: 파라미터를 **train(IS)에서만** 고르고 **test(OOS, 손 안 댄 구간)
에서만** 평가한다. 롤링 분할마다 IS 최고 조합을 뽑아 그 조합을 OOS에 적용, OOS 수익만
이어붙여 최종 판정에 쓴다(walkforward.concat_oos_returns).

grid는 좁게 유지한다 — 조정 손잡이가 많을수록 IS에 곡선맞춤될 위험과 다중비교 페널티
(Deflated Sharpe의 n_trials)가 커진다. 7개 이하 제약(04-data).

엔진은 파라미터에 의존하지 않는 피처를 매 호출 재계산하지만, 개발 규모(소수 종목)에선
무시할 비용이라 엔진 API를 건드리지 않는다(전종목 확장 시 피처 캐시 도입 검토).
"""
from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd

from backtest import engine
from backtest.walkforward import Split, concat_oos_returns, rolling_splits
from eval import metrics

# 튜닝 손잡이: (toml 섹션, 키) → 후보값들. 좁게 유지(과최적화·DSR 페널티).
_GRID: dict[tuple[str, str], list[float]] = {
    ("entry", "score_min"): [0.50, 0.55, 0.60],
    ("entry", "stop_atr_k"): [1.5, 2.0, 2.5],
    ("exits", "tp1_R"): [1.5, 2.0],
    ("exits", "trail_k"): [2.5, 3.0],
}


def param_grid(base: dict, grid: dict | None = None) -> list[dict]:
    """base 파라미터에 grid 손잡이 조합을 덮어쓴 후보 dict 리스트(데카르트 곱)."""
    g = grid or _GRID
    keys = list(g)
    out: list[dict] = []
    for combo in product(*(g[k] for k in keys)):
        p = copy.deepcopy(base)
        for (section, key), val in zip(keys, combo, strict=True):
            p[section][key] = val
        out.append(p)
    return out


def is_objective(result: engine.BacktestResult) -> float:
    """IS 선택 기준 = 연율 Sharpe. 거래가 없으면 후보 탈락(-inf)."""
    if not result.trades or result.equity.empty:
        return float("-inf")
    return metrics.sharpe(metrics.daily_returns(result.equity))


@dataclass
class WFRecord:
    """한 워크포워드 구간의 결과(IS에서 고른 조합 + OOS 성과)."""
    split: Split
    best_params: dict
    is_score: float
    oos_return: float
    oos_returns: pd.Series


def _run(prices, markets, p, *, start, end, capital, tax, feats=None, trend=None
         ) -> engine.BacktestResult:
    return engine.run(prices, markets, start=start, end=end,
                      initial_capital=capital, params=p, tax_params=tax,
                      feats=feats, market_trend=trend)


def _precompute_feats(prices: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """피처는 파라미터 무관 → 튜닝 대량호출 전 한 번만 계산(엔진 재계산 회피)."""
    return {c: engine.build_features(df) for c, df in prices.items()}


def walkforward_tune(
    prices: dict[str, pd.DataFrame],
    markets: dict[str, str],
    *,
    train_size: int,
    test_size: int,
    initial_capital: float,
    base_params: dict,
    step: int | None = None,
    anchored: bool = False,
    grid: dict | None = None,
    tax_params: dict | None = None,
    market_trend: dict | None = None,
    objective: Callable[[engine.BacktestResult], float] = is_objective,
) -> list[WFRecord]:
    """롤링 워크포워드 튜닝. 각 구간: IS에서 grid 전체 평가→최고 선택, OOS에서 그 1개만 평가."""
    dates = sorted({d for df in prices.values() for d in df.index})
    splits = rolling_splits(dates, train_size=train_size, test_size=test_size,
                            step=step, anchored=anchored)
    candidates = param_grid(base_params, grid)
    feats = _precompute_feats(prices)
    records: list[WFRecord] = []
    for sp in splits:
        # IS: 후보 전체 평가 → 최고 조합
        best_score, best_p = float("-inf"), candidates[0]
        for p in candidates:
            r = _run(prices, markets, p, start=sp.train_start, end=sp.train_end,
                     capital=initial_capital, tax=tax_params, feats=feats,
                     trend=market_trend)
            s = objective(r)
            if s > best_score:
                best_score, best_p = s, p
        # OOS: 최고 조합만 (손 안 댄 구간)
        oos = _run(prices, markets, best_p, start=sp.test_start, end=sp.test_end,
                   capital=initial_capital, tax=tax_params, feats=feats,
                   trend=market_trend)
        records.append(WFRecord(
            split=sp, best_params=best_p, is_score=best_score,
            oos_return=metrics.total_return(oos.equity),
            oos_returns=metrics.daily_returns(oos.equity),
        ))
    return records


def oos_equity(records: list[WFRecord], initial_capital: float = 1.0) -> pd.Series:
    """각 구간 OOS 일수익을 시간순으로 이어붙여 만든 전체 OOS equity 곡선."""
    rets = concat_oos_returns([list(r.oos_returns) for r in records])
    if not rets:
        return pd.Series(dtype=float)
    eq = initial_capital * np.cumprod(1.0 + np.asarray(rets, dtype=float))
    return pd.Series(np.concatenate([[initial_capital], eq]))


def recommend_params(records: list[WFRecord], base_params: dict,
                     grid: dict | None = None) -> dict:
    """구간별로 IS가 고른 값 중 **가장 자주 선택된 값**으로 손잡이를 확정(모달).

    OOS가 검증한 '안정적으로 좋았던' 설정을 고르는 보수적 방식 — 한 구간의 행운값에
    휘둘리지 않는다. 동률이면 base 값을 우선한다.
    """
    g = grid or _GRID
    out = copy.deepcopy(base_params)
    for (section, key) in g:
        votes = Counter(r.best_params[section][key] for r in records)
        if not votes:
            continue
        top = max(votes.values())
        winners = [v for v, c in votes.items() if c == top]
        base_val = base_params[section][key]
        out[section][key] = base_val if base_val in winners else winners[0]
    return out


def perf_matrix(
    prices: dict[str, pd.DataFrame],
    markets: dict[str, str],
    *,
    train_size: int,
    test_size: int,
    initial_capital: float,
    base_params: dict,
    step: int | None = None,
    grid: dict | None = None,
    tax_params: dict | None = None,
    market_trend: dict | None = None,
) -> np.ndarray:
    """PBO(CSCV)용 성과행렬: shape (n_splits, n_candidates), 각 칸=후보의 OOS 구간수익.

    eval.gate.pbo_cscv에 넣어 'IS 최고 조합이 OOS에서 중앙값 이하로 떨어지는 확률'을 잰다.
    """
    dates = sorted({d for df in prices.values() for d in df.index})
    splits = rolling_splits(dates, train_size=train_size, test_size=test_size,
                            step=step, anchored=False)
    candidates = param_grid(base_params, grid)
    feats = _precompute_feats(prices)
    mat = np.zeros((len(splits), len(candidates)))
    for i, sp in enumerate(splits):
        for j, p in enumerate(candidates):
            r = _run(prices, markets, p, start=sp.test_start, end=sp.test_end,
                     capital=initial_capital, tax=tax_params, feats=feats,
                     trend=market_trend)
            mat[i, j] = metrics.total_return(r.equity)
    return mat
