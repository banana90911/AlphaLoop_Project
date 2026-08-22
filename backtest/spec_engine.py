"""설계 정합 사이클 백테스트 엔진 (backtest/spec_engine).

기존 `backtest/engine.py`가 설계(03-arch 3-1 · 04-data 4.2 · 05-risk · 06-sizing)와
어긋난 채 만들어져 성과 수치가 설계와 무관해진 문제를 바로잡기 위해, 문서를 그대로
재현한 엔진이다. 문서와 다른 선택을 한 곳은 전부 아래에 사유와 함께 적는다.

**사이클 구조 (03-arch 3-1)**
- 장 시작 전 배치: 전 종목 점수를 *전일 확정 일봉*으로 산출(당일 정보 미사용).
- 정기 사이클: 하루 1회. `entry_timing`으로 시각을 고른다.
  - "first" = 첫 사이클(10:00) → 사이클 시점 가격 프록시는 **당일 시가**
  - "last"  = 마지막 사이클(14:30) → 프록시는 **당일 종가**
  일봉만으로는 장중 시각별 가격을 알 수 없으므로 시가·종가를 양 끝 프록시로 쓴다.
- 손절 스톱지정가는 KIS에 상시 걸려 있어 사이클과 무관하게 장중 체결된다(3-1 방치시간).
  일봉은 저가가 찍힌 시각을 알려주지 않으므로 **스톱 체결은 사이클 전에 일괄 판정**한다.
  두 `entry_timing`에 동일 규칙이라 비교는 공정하고, 갭 하락(시가 ≤ 스톱)은 지정가가
  무력화된 것이므로 선행 게이트의 시장가 강제 정리로 본다(3-1·05-risk A.1 1).

**결정·집행 순서 (03-arch 3-1 3단계)**
결정은 사이클 시작 시점 보유 스냅샷으로 ① 신규 진입 판정 → ② 보유 재평가 순서다.
따라서 그 사이클에 청산될 종목은 결정 시점엔 아직 보유라 신규 후보가 아니다(같은 사이클
매도→재매수 없음). 집행은 청산 먼저(자금 회수) → 신규 진입(5단계).

**문서에 있으나 이 엔진이 재현하지 못하는 것 (데이터 부재 — 결과 해석 시 감안)**
- 관리종목·거래정지·투자경고/위험·단기과열·VI·점상한가 상태(04-data ⓪ KIS 종목마스터
  상태값 미수집) → 제외 필터와 05-risk A.1 7 종목상태 게이트가 빠진다.
- 당일 급등 종목 편입(04-data 4.2) → 사이클 시점의 당일 누적 거래대금·등락률을 일봉으로
  알 수 없다. 종가로 대신하면 룩어헤드이므로 넣지 않는다.
- 수급 잠정치(04-data 4.3 ④ vintage 갭) → 캐시에 확정치만 있어 전일 확정치를 쓴다.
"""
from __future__ import annotations

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
    trades: list[ClosedTrade] = field(default_factory=list)
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    # 진단 카운터 — 설계 규칙이 실제로 몇 번 걸렸는지(사후 검증용)
    diag: dict = field(default_factory=dict)

    def total_return(self) -> float:
        if self.equity.empty:
            return 0.0
        return self.equity.iloc[-1] / self.equity.iloc[0] - 1.0


# 외부 호출 호환 별칭 — 피처 정의는 data.features 한 곳에만 있다(09-eval 9.6.3)
build_spec_features = build_features


def _wide(feats: dict[str, pd.DataFrame], col: str, dates: pd.Index) -> pd.DataFrame:
    """종목별 피처 → 날짜×종목 wide 표(횡단면 백분위를 벡터로 계산하기 위함)."""
    return pd.DataFrame(
        {c: f[col] for c, f in feats.items() if col in f.columns}
    ).reindex(dates)


def _panel_at(W: dict, prev: date, close: pd.Series) -> pd.DataFrame:
    """전일 확정 일봉 기준 횡단면 패널 — 운영 `data.panel.build_panel`과 같은 컬럼 구성."""
    cols = {c: W[c].loc[prev] for c in
            ("momentum", "supply20", "value", "lowvol", "adv20")}
    cols["close"] = close
    return pd.DataFrame(cols)


def _kelly_pb(trades: list[ClosedTrade], window: int = 100) -> tuple[float, float] | None:
    """최근 window건 청산으로 (승률 p, 손익비 b) — 06-sizing 6.1 ① "최근 100건 롤링"."""
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
    """설계 정합 백테스트. entry_timing ∈ {"first"(10:00≈시가), "last"(14:30≈종가)}.

    params_schedule: [(적용 시작일, params), …] 오름차순. 주면 그 날짜부터 해당 params를
    쓴다 — 워크포워드 OOS에서 *자본·보유는 이어가면서 파라미터만* 구간마다 갈아끼우기
    위한 것이다(구간마다 자본을 초기화하는 콜드스타트가 장기보유 모멘텀을 토막내
    자본 초기화가 장기보유를 토막내는 계측 결함을 피한다, 09-eval 9.6.5).
    """
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
    result = SpecResult()
    equity_points: dict[date, float] = {}
    diag = {
        "cycles": 0, "gap_stop": 0, "intraday_stop": 0, "entries": 0,
        "cycle_blocked": 0, "safestop": 0, "exposure_reject": 0, "adv_capped": 0,
        "kelly_active_days": 0, "same_day_reentry": 0, "eligible_avg": 0.0,
        "cash_short": 0, "avg_positions": 0.0, "avg_cash_ratio": 0.0, "buy_signals": 0,
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

        day_start_equity = cash + sum(
            pos.qty * (p_close.get(c) if pd.notna(p_close.get(c)) else pos.entry_price)
            for c, pos in positions.items()
        )
        closed_today: set[str] = set()

        # ── 사이클 전: 상시 걸린 손절 스톱 체결 판정 (03-arch 3-1 방치시간·05-risk A.1 1) ──
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
        for h in held:                     # 보유는 점수·필터 무관 항상 포함(04-data 4.2)
            if h not in watch:
                watch.append(h)

        # ── 3단계: 워치리스트 점수 (04-data 4.2 장중 갱신 정책) ──
        # 네 항목 모두 장중에 의미 있게 갱신되지 않아 점수는 전일 기준 그대로다. 다만 보유
        # 종목이 제외 필터에 걸려 elig에서 빠졌을 수 있어, 보유를 포함한 집합에서 다시 낸다
        # (백분위 모집단이 달라지므로 batch_score를 그대로 쓸 수 없다).
        # 사이클 시점 가격은 진입가·손절가 확정에만 쓴다.
        cyc_close = p_close.copy()
        for c in watch:
            v = px.get(c)
            if pd.notna(v):
                cyc_close[c] = v
        elig_cycle = panel.index.intersection(elig.union(pd.Index(held)))
        cycle_score = screener.score(panel.loc[elig_cycle], scr)

        # ── 4단계 앞부분: 사이클 게이트 (05-risk A.1 1~4 · 서킷브레이커) ──
        acc = _account(day_start_equity, cash, positions, cyc_close, markets)
        verdict = screen_cycle(MarketState(), acc, rp)
        diag["cycles"] += 1
        if verdict.action in ("halt", "skip"):
            equity_points[d] = _equity(cash, positions, row_close, p_close)
            continue
        new_allowed = verdict.action == "proceed"
        if not new_allowed:
            diag["cycle_blocked"] += 1

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
        # ② 보유 재평가 — 점수가 무효 임계 아래면 청산 제안(06-sizing 6.2 ①)
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
            # 켈리 천장은 청산 표본이 kelly_min_trades 이상일 때만 실제로 걸린다(06-sizing 6.1)
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
                # 유동성 한도: 주문금액 ≤ ADV20 × adv_participation (05-risk limits)
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
            # A.3 모델 이상행동 게이트 — 걸리면 그 사이클 신규 전량 취소(SafeStop)
            proposals = [OrderProposal(c, "buy", q * p) for c, q, p, _ in planned]
            if planned and not detect_anomaly(proposals, acc, rp):
                diag["safestop"] += 1
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
    result.equity = pd.Series(equity_points).sort_index()
    return result


def _account(
    start_capital: float, cash: float, positions: dict[str, _Holding],
    price_row: pd.Series, markets: dict[str, str],
) -> Account:
    ps = []
    for c, pos in positions.items():
        v = price_row.get(c)
        last = float(v) if pd.notna(v) else pos.entry_price
        ps.append(RiskPosition(c, pos.qty, last, markets.get(c, "KOSPI")))
    return Account(start_capital=start_capital, cash=cash, positions=ps)


def _proceeds(price: float, qty: int, market: str, d: date, tax_params) -> float:
    return price * qty - costs.trade_cost(price, qty, "sell", market, d,
                                          params=tax_params)["total"]


def _close(pos: _Holding, code: str, d: date, price: float, qty: int, reason: str,
           tax_params) -> ClosedTrade:
    buy_cost = costs.trade_cost(pos.entry_price, qty, "buy", pos.market,
                                pos.entry_date, params=tax_params)["total"]
    sell_cost = costs.trade_cost(price, qty, "sell", pos.market, d,
                                 params=tax_params)["total"]
    net = (price - pos.entry_price) * qty - buy_cost - sell_cost
    return ClosedTrade(code, pos.entry_date, d, pos.entry_price, price, qty, reason, net)


def _equity(cash: float, positions: dict[str, _Holding], row_close: pd.Series,
            p_close: pd.Series) -> float:
    held = 0.0
    for c, pos in positions.items():
        v = row_close.get(c)
        if pd.isna(v):
            v = p_close.get(c)
        held += (float(v) if pd.notna(v) else pos.entry_price) * pos.qty
    return cash + held
