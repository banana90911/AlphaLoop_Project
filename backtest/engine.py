"""백테스트 재생 엔진 (backtest/engine, 10장·05-risk). 직접 구현(zipline/vectorbt 미사용).

부품 결합: indicators(피처) → screener(워치리스트) → sizing(수량) → exits(청산) → costs(비용).

체결 모델(룩어헤드 차단): **모든 결정은 당일 종가까지의 정보만** 쓰고 당일 종가에 체결한다
(미래 데이터 미참조 = 룩어헤드 없음). 종가 동시성의 낙관은 슬리피지 비용으로 보정한다.
conviction은 스크리너 점수(코드 결정 = B안, LLM 미관여).

생존편향: 상폐 종목 가격도 prices에 넣으면 자동 반영된다(데이터가 있는 날까지만 거래).
현재 유니버스는 상장 종목 위주라 완전 차단은 상폐 종목 보강 후(데이터 레이어 과제).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import floor

import pandas as pd

from config.settings import load_params
from core import costs
from data import indicators as ind
from data import screener
from exec.exits import Position, decide_exit
from risk import sizing

_SCREEN_COLS = ["momentum", "supply", "lowvol", "alignment", "value_traded"]


@dataclass
class ClosedTrade:
    code: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    qty: int
    reason: str
    net_pnl: float           # 비용·세금 차감 후 손익(원)


@dataclass
class _Holding:
    market: str
    entry_price: float
    qty: int
    initial_stop: float
    current_stop: float
    entry_date: date
    days_held: int = 0
    tp1_done: bool = False


@dataclass
class BacktestResult:
    trades: list[ClosedTrade] = field(default_factory=list)
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))

    def total_return(self) -> float:
        if self.equity.empty:
            return 0.0
        return self.equity.iloc[-1] / self.equity.iloc[0] - 1.0


def build_features(df: pd.DataFrame, *, supply_window: int = 20) -> pd.DataFrame:
    """종목 OHLCV(+수급) → 스크리너·청산용 피처 시계열. index=date."""
    out = pd.DataFrame(index=df.index)
    out["close"] = df["close"]
    out["momentum"] = ind.momentum(df["close"], 252, skip=20)   # 12-1 모멘텀(04-data)
    out["atr"] = ind.atr(df["high"], df["low"], df["close"], 20)
    out["lowvol"] = ind.realized_vol(df["close"], 20)
    out["alignment"] = ind.alignment_score(df["close"])
    out["value_traded"] = df["close"] * df["volume"]
    if "foreign_net" in df or "inst_net" in df:
        flow = df.get("foreign_net", 0) + df.get("inst_net", 0)
        out["supply"] = ind.net_supply(flow, supply_window)
    return out


def run(
    prices: dict[str, pd.DataFrame],
    markets: dict[str, str],
    *,
    start: date,
    end: date,
    initial_capital: float,
    params: dict | None = None,
    tax_params: dict | None = None,
    feats: dict[str, pd.DataFrame] | None = None,
    market_trend: dict[str, pd.Series] | None = None,
) -> BacktestResult:
    """포트폴리오 백테스트. prices[code]: date 인덱스 OHLCV(+foreign_net/inst_net 선택).

    feats: 사전계산한 피처(파라미터 무관)를 주입하면 재계산을 건너뛴다(튜닝 시 대량 호출 가속).
    market_trend: 시장별 상승추세 bool 시계열(backtest/regime). 주면 하락추세 시장의
      신규 진입을 막는다(하락장 방어). None이면 방어 없음(기존 동작).
    """
    rp = params or load_params("risk_params")
    entry, limits, weights = rp["entry"], rp["limits"], rp["screener"]
    max_pos, top_n = limits["max_positions"], weights["top_n"]
    stop_k, score_min = entry["stop_atr_k"], entry["score_min"]

    if feats is None:
        feats = {c: build_features(df) for c, df in prices.items()}
    all_dates = sorted({d for df in prices.values() for d in df.index if start <= d <= end})

    cash = initial_capital
    positions: dict[str, _Holding] = {}
    result = BacktestResult()
    equity_points: dict[date, float] = {}

    for d in all_dates:
        # ── 1) 청산 (보유별 우선순위) ──
        for code in list(positions):
            f = feats[code]
            if d not in f.index:
                continue
            pos = positions[code]
            close, atr = f.at[d, "close"], f.at[d, "atr"]
            if pd.isna(close) or pd.isna(atr):
                continue
            act = decide_exit(
                Position(pos.entry_price, pos.initial_stop, pos.current_stop,
                         pos.days_held, pos.tp1_done),
                close, atr, params=rp,
            )
            if act.action == "exit_full":
                cash += _proceeds(close, pos.qty, pos.market, d, tax_params)
                result.trades.append(_close(pos, code, d, close, pos.qty, act.reason, tax_params))
                del positions[code]
                continue
            if act.action == "exit_partial":
                sq = floor(pos.qty * act.fraction)
                if sq > 0:
                    cash += _proceeds(close, sq, pos.market, d, tax_params)
                    result.trades.append(_close(pos, code, d, close, sq, act.reason, tax_params))
                    pos.qty -= sq
                pos.tp1_done = True
                if act.new_stop is not None:
                    pos.current_stop = act.new_stop
            elif act.action == "raise_stop" and act.new_stop is not None:
                pos.current_stop = act.new_stop
            pos.days_held += 1

        # ── 2) 신규 진입 ──
        slots = max_pos - len(positions)
        if slots > 0:
            panel = _cross_section(feats, d, prices)
            if not panel.empty:
                wl = screener.screen(panel, weights=weights, top_n=top_n,
                                     holdings=tuple(positions))
                equity_now = _equity(cash, positions, feats, d)
                for code, row in wl.iterrows():
                    if slots <= 0:
                        break
                    if code in positions or row["score"] < score_min:
                        continue
                    # 하락장 방어: 해당 종목 시장이 하락추세면 신규 진입 안 함
                    if market_trend is not None:
                        tr = market_trend.get(markets[code])
                        if tr is not None and not bool(tr.get(d, True)):
                            continue
                    f = feats[code]
                    close, atr, mom = f.at[d, "close"], f.at[d, "atr"], f.at[d, "momentum"]
                    # 워밍업 미완(momentum NaN) 또는 하락 모멘텀이면 진입 안 함
                    # (모멘텀 전략의 절대 게이트 — 스크리너 백분위는 상대순위라 하락장도 1등이 생김)
                    if pd.isna(close) or pd.isna(atr) or atr <= 0 or pd.isna(mom) or mom <= 0:
                        continue
                    stop = close - stop_k * atr
                    if stop <= 0:
                        continue
                    qty = sizing.position_qty(equity_now, close, stop,
                                              conviction=float(row["score"]), params=rp)
                    if qty <= 0:
                        continue
                    cost = costs.trade_cost(close, qty, "buy", markets[code], d, params=tax_params)
                    spend = close * qty + cost["total"]
                    if spend > cash:
                        continue
                    cash -= spend
                    positions[code] = _Holding(markets[code], close, qty, stop, stop, d)
                    slots -= 1

        equity_points[d] = _equity(cash, positions, feats, d)

    result.equity = pd.Series(equity_points).sort_index()
    return result


def _proceeds(price: float, qty: int, market: str, d: date, tax_params: dict | None) -> float:
    cost = costs.trade_cost(price, qty, "sell", market, d, params=tax_params)
    return price * qty - cost["total"]


def _close(pos: _Holding, code: str, d: date, price: float, qty: int, reason: str,
           tax_params: dict | None) -> ClosedTrade:
    buy_cost = costs.trade_cost(pos.entry_price, qty, "buy", pos.market, pos.entry_date,
                               params=tax_params)["total"]
    sell_cost = costs.trade_cost(price, qty, "sell", pos.market, d, params=tax_params)["total"]
    net = (price - pos.entry_price) * qty - buy_cost - sell_cost
    return ClosedTrade(code, pos.entry_date, d, pos.entry_price, price, qty, reason, net)


def _cross_section(feats: dict[str, pd.DataFrame], d: date,
                   prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = {}
    for code, f in feats.items():
        if d in f.index and not pd.isna(f.at[d, "close"]):
            rows[code] = {c: f.at[d, c] for c in _SCREEN_COLS if c in f.columns}
    return pd.DataFrame.from_dict(rows, orient="index")


def _equity(cash: float, positions: dict[str, _Holding],
            feats: dict[str, pd.DataFrame], d: date) -> float:
    held = 0.0
    for code, pos in positions.items():
        f = feats[code]
        has_price = d in f.index and not pd.isna(f.at[d, "close"])
        price = f.at[d, "close"] if has_price else pos.entry_price
        held += price * pos.qty
    return cash + held
