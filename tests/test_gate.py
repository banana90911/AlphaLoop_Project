"""Go/No-Go 게이트 — DSR·PBO·방향성 (eval/gate, 10-1)."""
import numpy as np

from eval import gate


def test_expected_max_sharpe_grows_with_trials():
    # 시도 횟수 많을수록 우연 최대 Sharpe 기대값 ↑
    assert gate.expected_max_sharpe(100, 1.0) > gate.expected_max_sharpe(10, 1.0)


def test_deflated_sharpe_penalizes_many_trials():
    # 같은 관측 Sharpe라도 시도가 많으면 DSR ↓(운일 확률 ↑)
    few = gate.deflated_sharpe(0.15, n_obs=1000, n_trials=5, sr_std=0.05)
    many = gate.deflated_sharpe(0.15, n_obs=1000, n_trials=500, sr_std=0.05)
    assert few > many


def test_deflated_sharpe_in_unit_interval():
    d = gate.deflated_sharpe(0.1, n_obs=500, n_trials=20, sr_std=0.05)
    assert 0.0 <= d <= 1.0


def test_pbo_random_near_half():
    # 순수 난수 성과 → IS 우수가 OOS로 안 이어짐 → PBO 0.5 근처
    rng = np.random.default_rng(0)
    perf = rng.standard_normal((200, 20))
    pbo = gate.pbo_cscv(perf, n_splits=10)
    assert 0.3 < pbo < 0.7


def test_pbo_consistent_config_low():
    # 한 조합이 모든 기간에서 일관 우수 → PBO 낮음
    rng = np.random.default_rng(1)
    perf = rng.standard_normal((200, 20)) * 0.1
    perf[:, 0] += 5.0                       # config 0이 항상 압도적
    pbo = gate.pbo_cscv(perf, n_splits=10)
    assert pbo < 0.1


def test_pbo_invalid_shape_raises():
    import pytest
    with pytest.raises(ValueError):
        gate.pbo_cscv(np.zeros((5, 1)), n_splits=10)


def test_directional_gate_all_pass():
    r = gate.directional_gate(
        strategy_score=0.30,
        benchmark_scores={"kospi": 0.10, "momentum": 0.15, "cash": 0.0, "equal": 0.12},
        dsr=0.97, pbo=0.30,
        sensitivity_no_cliff=True, stress_beats_benchmarks=True,
    )
    assert r.passed
    assert all(r.checks.values())


def test_directional_gate_fails_if_one_misses():
    # 벤치마크 하나 못 이김 → 전체 No-Go
    r = gate.directional_gate(
        strategy_score=0.11,
        benchmark_scores={"kospi": 0.10, "momentum": 0.15, "cash": 0.0, "equal": 0.12},
        dsr=0.97, pbo=0.30,
        sensitivity_no_cliff=True, stress_beats_benchmarks=True,
    )
    assert not r.passed
    assert not r.checks["beats_all_benchmarks"]


def test_directional_gate_fails_high_pbo():
    r = gate.directional_gate(
        strategy_score=0.30,
        benchmark_scores={"kospi": 0.10},
        dsr=0.97, pbo=0.60,                # 과최적화
        sensitivity_no_cliff=True, stress_beats_benchmarks=True,
    )
    assert not r.passed
    assert not r.checks["pbo_below_50pct"]
