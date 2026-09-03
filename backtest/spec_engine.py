"""
description:        설계 정합 사이클 백테스트 엔진 (문서 재현, 구 engine.py 대체)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from dataclasses import dataclass, field
from datetime import date
from math import floor

import numpy as np
import pandas as pd

from config.settings import load_params
from core import costs
from data import features, screener
from data.features import build_features
from exec.exits import Position, decide_exit
from risk import sizing
from risk.risk_engine import (
    Account,
    MarketState,
    OrderProposal,
    Position as RiskPosition,
    check_new_buy,
    detect_anomaly,
    screen_cycle,
)

@dataclass
class ClosedTrade:
    """청산 완료 거래 1건."""
    code: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    qty: int
    reason: str
    net_pnl: float


@dataclass
class _Holding:
    """엔진 내부 보유 상태(포지션 스냅샷)."""
    market: str
    entry_price: float
    qty: int
    initial_stop: float
    current_stop: float
    entry_date: date
    days_held: int = 0
    breakeven_done: bool = False


@dataclass
class SpecResult:
    """백테스트 실행 결과 — 거래 목록·equity 곡선·진단 카운터."""
    trades: list[ClosedTrade] = field(default_factory=list)
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    # 진단 카운터 — 설계 규칙이 실제로 몇 번 걸렸는지(사후 검증용)
    diag: dict = field(default_factory=dict)
    # SafeStop이 걸린 날. 값이 있으면 그 날 이후 신규 진입이 없다는 뜻이다
    safe_stop_date: date | None = None

    def total_return(self) -> float:
        """누적수익률을 계산한다."""
        if self.equity.empty:
            return 0.0
        return self.equity.iloc[-1] / self.equity.iloc[0] - 1.0


# 외부 호출 호환 별칭 — 피처 정의는 data.features 한 곳에만 있다
build_spec_features = build_features


def _wide(feats: dict[str, pd.DataFrame], col: str, dates: pd.Index) -> pd.DataFrame:
    """종목별 피처를 날짜×종목 wide 표로 만든다(횡단면 벡터 연산용)."""
    return pd.DataFrame(
        {c: f[col] for c, f in feats.items() if col in f.columns}
    ).reindex(dates)


def _panel_at(W: dict, prev: date, close: pd.Series) -> pd.DataFrame:
    """전일 확정 일봉 기준 횡단면 패널을 만든다(운영 build_panel과 같은 컬럼 구성)."""
    cols = {c: W[c].loc[prev] for c in
            ("momentum", "supply20", "value", "lowvol", "adv20")}
    cols["close"] = close
    return pd.DataFrame(cols)


def _kelly_pb(trades: list[ClosedTrade], window: int = 100) -> tuple[float, float] | None:
    """최근 window건 청산으로 (승률 p, 손익비 b)를 계산한다."""
    recent = trades[-window:]
    wins = [t.net_pnl for t in recent if t.net_pnl > 0]
    losses = [-t.net_pnl for t in recent if t.net_pnl < 0]
    if not wins or not losses:
        return None
    return len(wins) / len(recent), (sum(wins) / len(wins)) / (sum(losses) / len(losses))


def run(
    prices: dict[str, pd.DataFrame],
    markets: dict[str, str],
    *,
    start: date,
    end: date,
    initial_capital: float,
    entry_timing: str = "last",
    params: dict | None = None,
    tax_params: dict | None = None,
    feats: dict[str, pd.DataFrame] | None = None,
    params_schedule: list[tuple[date, dict]] | None = None,
) -> SpecResult:
    """설계 정합 백테스트를 실행한다. entry_timing ∈ {"first"(≈시가), "last"(≈종가)}."""
    if entry_timing not in ("first", "last"):
        raise ValueError("entry_timing은 first|last")
    base_rp = params or load_params("risk_params")

    def _params_at(d: date) -> dict:
        if not params_schedule:
            return base_rp
        cur = base_rp
        for start_d, p in params_schedule:
            if start_d <= d:
                cur = p
            else:
                break
        return cur

    if feats is None:
        feats = {c: build_spec_features(df) for c, df in prices.items()}
    all_dates = pd.Index(sorted({d for f in feats.values() for d in f.index}))

    # 지표별 wide 표(날짜×종목) — 매일 한 행씩 뽑아 횡단면을 벡터로 처리
    W = {
        col: _wide(feats, col, all_dates)
        for col in ("open", "high", "low", "close", "momentum", "atr", "lowvol",
                    "adv20", "value", "supply20")
    }

    cash = initial_capital
    positions: dict[str, _Holding] = {}
    # SafeStop은 한 번 걸리면 풀리지 않는다(사람 개입 필수 — 05-risk 5.4)
    safe_stopped = False
    safe_stop_date: date | None = None
    result = SpecResult()
    equity_points: dict[date, float] = {}
    diag = {
        "cycles": 0, "gap_stop": 0, "intraday_stop": 0, "entries": 0,
        "cycle_blocked": 0, "safestop": 0, "exposure_reject": 0, "adv_capped": 0,
        "kelly_active_days": 0, "same_day_reentry": 0, "eligible_avg": 0.0,
        "cash_short": 0, "cost_exceeds_edge": 0, "safestop_blocked_days": 0,
        "avg_positions": 0.0, "avg_cash_ratio": 0.0, "buy_signals": 0,
        "qty_zero_kelly": 0, "qty_zero_vol": 0, "no_slot": 0, "kelly_f_neg_days": 0,
    }
    elig_counts: list[int] = []
    pos_counts: list[int] = []
    cash_ratios: list[float] = []

    trade_dates = [d for d in all_dates if start <= d <= end]
    for d in trade_dates:
        i = all_dates.get_loc(d)
        if i == 0:
            continue
        prev = all_dates[i - 1]            # 배치는 전일 확정 일봉만 본다
        rp = _params_at(d)
        scr, dec, ent, lim = rp["screener"], rp["decision"], rp["entry"], rp["limits"]
        top_n, max_pos = int(scr["top_n"]), int(lim["max_positions"])
        entry_thr = float(dec["entry_threshold"])
        exit_thr = float(dec["exit_threshold"])
        stop_k = float(ent["stop_atr_k"])
        adv_part = float(lim["adv_participation"])
        row_open, row_close = W["open"].loc[d], W["close"].loc[d]
        row_low, row_high = W["low"].loc[d], W["high"].loc[d]
        p_close, p_atr = W["close"].loc[prev], W["atr"].loc[prev]

        # 서킷브레이커 기준선 — 전일 종가 기준 총자산. 실거래의 `Account.start_capital`
        # (= AccountSnapshots.BaseAsset, 직전 거래일 마지막 총자산)과 같은 정의다.
        # 백테스트에는 입출금이 없으므로 `Account.net_external_flow`는 기본값 0.0을 쓰고,
        # 그때 daily_loss_pct는 예전 식과 1원도 다르지 않다(10-ops 10.9 동일성).
        day_start_equity = cash + sum(
            pos.qty * (p_close.get(c) if pd.notna(p_close.get(c)) else pos.entry_price)
            for c, pos in positions.items()
        )
        closed_today: set[str] = set()

        # ── 사이클 전: 상시 걸린 손절 스톱 체결 판정 ──
        for code in list(positions):
            pos = positions[code]
            o, lo = row_open.get(code), row_low.get(code)
            if pd.isna(o) or pd.isna(lo):
                continue
            if o <= pos.current_stop:      # 갭 하락 → 지정가 무력화 → 시장가 강제 정리
                fill, kind = float(o), "gap_stop"
            elif lo <= pos.current_stop:   # 장중 스톱 지정가 체결
                fill, kind = float(pos.current_stop), "intraday_stop"
            else:
                continue
            cash += _proceeds(fill, pos.qty, pos.market, d, tax_params)
            result.trades.append(_close(pos, code, d, fill, pos.qty, kind, tax_params))
            del positions[code]
            closed_today.add(code)
            diag[kind] += 1

        # ── 사이클 시점 가격 (일봉 프록시) ──
        px = row_open if entry_timing == "first" else row_close

        # ── 1단계: 후보 선별 (전일 확정 일봉 = 장 시작 전 배치 결과) ──
        panel = _panel_at(W, prev, p_close)
        elig = features.eligible(panel)
        elig_counts.append(len(elig))
        held = tuple(positions)
        if len(elig) == 0 and not held:
            equity_points[d] = _equity(cash, positions, row_close, p_close)
            continue

        batch_score = screener.score(panel.loc[elig], scr)
        watch = list(batch_score.sort_values(ascending=False).head(top_n).index)
        for h in held:                     # 보유는 점수·필터 무관 항상 포함
            if h not in watch:
                watch.append(h)

        # ── 3단계: 워치리스트 점수 재계산(보유 포함 집합에서, 진입가는 사이클 시점가) ──
        cyc_close = p_close.copy()
        for c in watch:
            v = px.get(c)
            if pd.notna(v):
                cyc_close[c] = v
        elig_cycle = panel.index.intersection(elig.union(pd.Index(held)))
        cycle_score = screener.score(panel.loc[elig_cycle], scr)

        # ── 4단계 앞부분: 사이클 게이트(서킷브레이커 등) ──
        acc = _account(day_start_equity, cash, positions, cyc_close, markets)
        verdict = screen_cycle(MarketState(), acc, rp)
        diag["cycles"] += 1
        if verdict.action in ("halt", "skip"):
            equity_points[d] = _equity(cash, positions, row_close, p_close)
            continue
        new_allowed = verdict.action == "proceed" and not safe_stopped
        if not new_allowed:
            diag["cycle_blocked"] += 1
        if safe_stopped:
            diag["safestop_blocked_days"] += 1

        # ── 3단계 결정 (사이클 시작 보유 스냅샷 기준) ──
        # ① 신규 진입 판정
        buys: list[str] = []
        if new_allowed:
            for c in watch:
                if c in positions or c not in cycle_score.index:
                    continue
                if float(cycle_score[c]) >= entry_thr:
                    buys.append(c)
            diag["buy_signals"] += len(buys)
        # ② 보유 재평가 — 점수가 무효 임계 아래면 청산 제안
        sells = {
            c for c in positions
            if c in cycle_score.index and float(cycle_score[c]) < exit_thr
        }

        # ── 5단계 집행 (a) 청산 — 자금 회수 먼저 ──
        for code in list(positions):
            pos = positions[code]
            price, a = px.get(code), p_atr.get(code)
            if pd.isna(price) or pd.isna(a) or a <= 0:
                continue
            act = decide_exit(
                Position(pos.entry_price, pos.initial_stop, pos.current_stop,
                         pos.days_held, pos.breakeven_done,
                         thesis_valid=code not in sells),
                float(price), float(a), params=rp,
            )
            if act.action == "exit_full":
                cash += _proceeds(float(price), pos.qty, pos.market, d, tax_params)
                result.trades.append(
                    _close(pos, code, d, float(price), pos.qty, act.reason, tax_params)
                )
                del positions[code]
                closed_today.add(code)
                continue
            if act.action == "exit_partial":
                sq = floor(pos.qty * act.fraction)
                if sq > 0:
                    cash += _proceeds(float(price), sq, pos.market, d, tax_params)
                    result.trades.append(
                        _close(pos, code, d, float(price), sq, act.reason, tax_params)
                    )
                    pos.qty -= sq
                pos.breakeven_done = True
                if act.new_stop is not None:
                    pos.current_stop = act.new_stop
            elif act.action == "raise_stop" and act.new_stop is not None:
                pos.current_stop = act.new_stop
                if act.reason == "breakeven":
                    pos.breakeven_done = True
            pos.days_held += 1

        # ── 4단계 뒷부분 + 5단계 (b) 신규 진입 ──
        if new_allowed and buys:
            pb = _kelly_pb(result.trades)
            # 켈리 천장은 청산 표본이 kelly_min_trades 이상일 때만 실제로 걸린다
            if pb is not None and len(result.trades) >= int(rp["sizing"]["kelly_min_trades"]):
                diag["kelly_active_days"] += 1
            planned: list[tuple[str, int, float, float]] = []
            acc = _account(day_start_equity, cash, positions, cyc_close, markets)
            slots = max_pos - len(positions)
            if pb is not None and len(result.trades) >= int(rp["sizing"]["kelly_min_trades"]):
                _f = sizing.kelly_fraction(pb[0], pb[1], float(rp["sizing"]["kelly_fraction"]))
                if _f <= 0:
                    diag["kelly_f_neg_days"] += 1
            for code in buys:
                if slots <= 0:
                    diag["no_slot"] += 1
                    break
                price, a = px.get(code), p_atr.get(code)
                if pd.isna(price) or pd.isna(a) or a <= 0:
                    continue
                mom = W["momentum"].loc[prev].get(code)
                if pd.isna(mom) or mom <= 0:   # 워밍업 미완·하락 모멘텀 무진입
                    continue
                stop = float(price) - stop_k * float(a)
                if stop <= 0:
                    continue
                # 유동성 한도: 주문금액 ≤ ADV20 × adv_participation
                adv = W["adv20"].loc[prev].get(code)
                caps: tuple[int, ...] = ()
                if pd.notna(adv) and adv > 0:
                    cap = int(adv * adv_part / float(price))
                    caps = (cap,)
                qty = sizing.position_qty(
                    acc.equity, float(price), stop,
                    conviction=float(cycle_score[code]),
                    p=None if pb is None else pb[0], b=None if pb is None else pb[1],
                    n=len(result.trades), extra_caps=caps, params=rp,
                )
                if caps and qty == caps[0]:
                    diag["adv_capped"] += 1
                if qty <= 0:
                    qv = sizing.position_qty(
                        acc.equity, float(price), stop,
                        conviction=float(cycle_score[code]),
                        extra_caps=caps, params=rp,
                    )
                    diag["qty_zero_kelly" if qv > 0 else "qty_zero_vol"] += 1
                    continue
                # 3단계 무거래 — 기대이익이 왕복 거래비용을 못 넘으면 사지 않는다(06-sizing 6.1)
                edge = costs.entry_edge(
                    float(price), stop, qty, markets[code], d,
                    reward_r=float(rp["exits"]["breakeven_R"]), params=tax_params,
                )
                if edge["net_edge"] <= 0:
                    diag["cost_exceeds_edge"] += 1
                    continue
                value = float(price) * qty
                if not check_new_buy(acc, code, value, rp):       # 총노출 하드룰
                    diag["exposure_reject"] += 1
                    continue
                cost = costs.trade_cost(float(price), qty, "buy", markets[code], d,
                                        params=tax_params)
                spend = value + cost["total"]
                if spend > cash:
                    diag["cash_short"] += 1
                    continue
                planned.append((code, qty, float(price), stop))
                cash -= spend
                acc = _account(day_start_equity, cash,
                               {**positions, **{c: _Holding(markets[c], p, q, s, s, d)
                                                for c, q, p, s in planned}},
                               cyc_close, markets)
                slots -= 1
            # A.3 모델 이상행동 게이트 — 걸리면 그 사이클 신규 전량 취소(SafeStop).
            # 이후로는 신규 진입을 영구 차단한다: 코드 고장이라 사람이 원인을 고치기
            # 전에는 재개하지 않으며(05-risk 5.4), 백테스트에는 고쳐줄 사람이 없다.
            # 보유 청산·손절은 계속 돈다 — 실거래의 미해제 SafeStop과 같은 동작이다.
            proposals = [OrderProposal(c, "buy", q * p) for c, q, p, _ in planned]
            if planned and not detect_anomaly(proposals, acc, rp):
                diag["safestop"] += 1
                safe_stopped = True
                if safe_stop_date is None:
                    safe_stop_date = d
                for c, q, p, _ in planned:
                    cost = costs.trade_cost(p, q, "buy", markets[c], d, params=tax_params)
                    cash += p * q + cost["total"]
            else:
                for c, q, p, s in planned:
                    positions[c] = _Holding(markets[c], p, q, s, s, d)
                    diag["entries"] += 1
                    if c in closed_today:
                        diag["same_day_reentry"] += 1

        eq_d = _equity(cash, positions, row_close, p_close)
        equity_points[d] = eq_d
        pos_counts.append(len(positions))
        cash_ratios.append(cash / eq_d if eq_d > 0 else 1.0)

    diag["avg_positions"] = float(np.mean(pos_counts)) if pos_counts else 0.0
    diag["avg_cash_ratio"] = float(np.mean(cash_ratios)) if cash_ratios else 0.0
    diag["eligible_avg"] = float(np.mean(elig_counts)) if elig_counts else 0.0
    result.diag = diag
    result.safe_stop_date = safe_stop_date
    result.equity = pd.Series(equity_points).sort_index()
    return result


def _account(
    start_capital: float, cash: float, positions: dict[str, _Holding],
    price_row: pd.Series, markets: dict[str, str],
) -> Account:
    """현재 시점의 Account 스냅샷을 만든다."""
    ps = []
    for c, pos in positions.items():
        v = price_row.get(c)
        last = float(v) if pd.notna(v) else pos.entry_price
        ps.append(RiskPosition(c, pos.qty, last, markets.get(c, "KOSPI")))
    return Account(start_capital=start_capital, cash=cash, positions=ps)


def _proceeds(price: float, qty: int, market: str, d: date, tax_params) -> float:
    """매도 실수령액(비용 차감 후)을 계산한다."""
    return price * qty - costs.trade_cost(price, qty, "sell", market, d,
                                          params=tax_params)["total"]


def _close(pos: _Holding, code: str, d: date, price: float, qty: int, reason: str,
           tax_params) -> ClosedTrade:
    """청산 1건을 ClosedTrade로 기록한다(net_pnl은 비용 차감 후)."""
    buy_cost = costs.trade_cost(pos.entry_price, qty, "buy", pos.market,
                                pos.entry_date, params=tax_params)["total"]
    sell_cost = costs.trade_cost(price, qty, "sell", pos.market, d,
                                 params=tax_params)["total"]
    net = (price - pos.entry_price) * qty - buy_cost - sell_cost
    return ClosedTrade(code, pos.entry_date, d, pos.entry_price, price, qty, reason, net)


def _equity(cash: float, positions: dict[str, _Holding], row_close: pd.Series,
            p_close: pd.Series) -> float:
    """현재 시점 총자산(현금+보유 평가액)을 계산한다."""
    held = 0.0
    for c, pos in positions.items():
        v = row_close.get(c)
        if pd.isna(v):
            v = p_close.get(c)
        held += (float(v) if pd.notna(v) else pos.entry_price) * pos.qty
    return cash + held
