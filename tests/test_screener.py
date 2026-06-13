"""스크리너 — 백분위 점수·방향·top_n·보유 포함 (data/screener, 04-data)."""
import pandas as pd

from data import screener

_W = {"w_momentum": 0.35, "w_supply": 0.25, "w_lowvol": 0.15,
      "w_alignment": 0.10, "w_value": 0.15}


def test_higher_momentum_scores_higher():
    panel = pd.DataFrame(
        {"momentum": [0.30, 0.10, -0.05]},
        index=["A", "B", "C"],
    )
    sc = screener.score(panel, {"w_momentum": 1.0})
    assert sc["A"] > sc["B"] > sc["C"]


def test_lowvol_direction_inverted():
    # 변동성 낮은 종목이 높은 점수
    panel = pd.DataFrame({"lowvol": [0.1, 0.5, 0.9]}, index=["A", "B", "C"])
    sc = screener.score(panel, {"w_lowvol": 1.0})
    assert sc["A"] > sc["C"]


def test_missing_indicator_is_neutral():
    # value 컬럼 없음 → 중립 0.5, 점수 계산은 momentum만으로
    panel = pd.DataFrame({"momentum": [0.3, 0.1]}, index=["A", "B"])
    sc = screener.score(panel, {"w_momentum": 0.5, "w_value": 0.5})
    assert 0.0 <= sc["A"] <= 1.0


def test_screen_top_n_and_holdings():
    panel = pd.DataFrame(
        {"momentum": [0.5, 0.4, 0.3, 0.2, 0.1]},
        index=["A", "B", "C", "D", "E"],
    )
    # top_n=2 → A,B. 보유 E는 점수 낮아도 포함
    wl = screener.screen(panel, weights=_W, top_n=2, holdings=("E",))
    assert "A" in wl.index and "B" in wl.index
    assert "E" in wl.index
    assert "C" not in wl.index


def test_liquidity_filter_excludes_illiquid():
    panel = pd.DataFrame({
        "momentum": [0.5, 0.4, 0.3],
        "value_traded": [1e9, 1e6, 1e9],   # B는 거래대금 미달
    }, index=["A", "B", "C"])
    wl = screener.screen(panel, weights={"w_momentum": 1.0}, top_n=5,
                         min_value_traded=1e8)
    assert "B" not in wl.index
    assert "A" in wl.index and "C" in wl.index


def test_liquidity_filter_exempts_holdings():
    panel = pd.DataFrame({
        "momentum": [0.5, 0.4],
        "value_traded": [1e9, 1e6],        # B 미달이나 보유라 면제
    }, index=["A", "B"])
    wl = screener.screen(panel, weights={"w_momentum": 1.0}, top_n=5,
                         holdings=("B",), min_value_traded=1e8)
    assert "B" in wl.index
