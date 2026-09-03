"""
description:        장 시작 전 일일 배치 진입점 (전종목 데이터·점수 준비)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import argparse
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from broker.kis_client import KISClient
from config.settings import get_settings, load_params
from core.timeutils import kst_today, now_utc
from core.trading_days import is_trading_day, previous_trading_day
from data import screener
from data.features import eligible
from data.panel import build_panel
from data.sources import index_history, kis_history, universe
from memory import journal
from memory.db import init_db

# 12-1 모멘텀(252거래일) + 20일 스킵 + 휴장 여유. 점수 계산이 읽어갈 최소 이력이다.
SCORE_LOOKBACK_DAYS = 450
# 지수 200일선 워밍업.
INDEX_LOOKBACK_DAYS = 400
# 증권그룹 ST(주권) → 07-model SecurityType. 마스터는 보통주만 걸러 받는다.
SECURITY_TYPE = "common"


@dataclass
class StepResult:
    """한 단계의 결과 — 그대로 `IngestRuns` 한 행이 된다."""
    target_table: str
    source: str
    status: str                 # ok / partial / failed
    target_count: int = 0
    success_count: int = 0
    rows_written: int = 0
    error_message: str | None = None


def _run_id(trade_date: date, table: str) -> str:
    """같은 날 같은 표의 재실행이 행을 늘리지 않도록 결정론 키를 쓴다."""
    return f"{trade_date:%Y%m%d}_{table}"


def _record(conn, trade_date: date, started, res: StepResult) -> None:
    """한 단계 결과를 IngestRuns에 기록한다."""
    journal.record_ingest_run(
        conn, run_id=_run_id(trade_date, res.target_table),
        target_table=res.target_table, source=res.source, status=res.status,
        started_at=started, range_start=trade_date, range_end=trade_date,
        target_count=res.target_count, success_count=res.success_count,
        rows_written=res.rows_written, error_message=res.error_message,
    )


# ── ① 종목 명부 ──────────────────────────────────────────────────
def ingest_symbols(conn) -> StepResult:
    """KIS 종목마스터(코스피·코스닥) → `Symbols` 적재(보통주만)."""
    try:
        df = universe.fetch_universe(common_only=True)
    except Exception as e:
        return StepResult("Symbols", "kis_mst", "failed",
                          error_message=f"{type(e).__name__}: {e}")
    rows = [
        {"code": r.code, "name": r.name, "market": r.market,
         "security_type": SECURITY_TYPE}
        for r in df.itertuples()
    ]
    n = journal.upsert_symbols(conn, rows)
    return StepResult("Symbols", "kis_mst", "ok", len(rows), len(rows), n)


# ── ② 일봉·수급 ──────────────────────────────────────────────────
def ingest_bars_and_flows(
    conn, client: KISClient, codes: list[str], *, start: date, end: date,
    skip_done: set[str] | None = None,
) -> tuple[StepResult, StepResult]:
    """종목별 일봉·수급을 `DailyBars`·`DailyFlows`에 적재한다(한 종목 실패는 격리)."""
    skip_done = skip_done or set()
    targets = [c for c in codes if c not in skip_done]
    bar_rows = flow_rows = bar_ok = flow_ok = 0
    errors: list[str] = []
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    for code in targets:
        try:
            df = kis_history.fetch_ohlcv_range(client, code, s, e)
        except Exception as ex:
            errors.append(f"{code} bars {type(ex).__name__}")
            continue
        if df.empty:
            continue
        bar_rows += journal.upsert_daily_bars(conn, code, df.to_dict("records"))
        bar_ok += 1
        try:                                    # 수급 실패는 일봉을 막지 않는다
            flows = _recent_flows(client, code, start)
        except Exception as ex:
            errors.append(f"{code} flows {type(ex).__name__}")
            continue
        if flows:
            flow_rows += journal.upsert_daily_flows(conn, code, flows)
            flow_ok += 1

    def status(ok: int) -> str:
        if ok == 0 and targets:
            return "failed"
        return "ok" if ok == len(targets) else "partial"

    msg = "; ".join(errors[:20]) or None
    return (
        StepResult("DailyBars", "kis_daily_chart", status(bar_ok),
                   len(targets), bar_ok, bar_rows, msg),
        StepResult("DailyFlows", "kis_investor", status(flow_ok),
                   len(targets), flow_ok, flow_rows, msg),
    )


def _recent_flows(client: KISClient, code: str, start: date) -> list[dict]:
    """투자자 순매수(최근 ≤30거래일) 중 start 이후분만 반환한다."""
    from data.market_data import fetch_supply

    df = fetch_supply(client, code)
    if df.empty:
        return []
    df = df[df.index >= start]
    return [
        {"date": idx, "foreign_net": r.get("foreign_net"), "inst_net": r.get("inst_net")}
        for idx, r in df.iterrows()
    ]


# ── ③ 지수 ───────────────────────────────────────────────────────
def ingest_indices(conn, *, start: date, end: date) -> StepResult:
    """코스피·코스닥 종가와 200일선을 `MarketIndices`에 적재한다."""
    written = ok = 0
    errors: list[str] = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = index_history.fetch_index(
                market, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            )
        except Exception as e:
            errors.append(f"{market} {type(e).__name__}")
            continue
        df = df.sort_values("date")
        df["sma200"] = df["close"].rolling(200).mean()
        df["regime"] = None
        has_ma = df["sma200"].notna()
        df.loc[has_ma, "regime"] = pd.Series(
            ["uptrend" if c >= m else "downtrend"
             for c, m in zip(df.loc[has_ma, "close"], df.loc[has_ma, "sma200"],
                             strict=True)],
            index=df.index[has_ma],
        )
        rows = [
            {"date": r.date, "close": r.close,
             "sma200": None if pd.isna(r.sma200) else r.sma200, "regime": r.regime}
            for r in df.itertuples()
        ]
        written += journal.upsert_market_index(conn, market, rows)
        ok += 1
    status = "ok" if ok == 2 else ("partial" if ok else "failed")
    return StepResult("MarketIndices", "yfinance", status, 2, ok, written,
                      "; ".join(errors) or None)


# ── ④ 전 종목 점수 ───────────────────────────────────────────────
def compute_daily_scores(conn, *, trade_date: date) -> StepResult:
    """DB에 쌓인 일봉·수급으로 전 종목 점수를 계산해 `DailyScores`에 적재한다."""
    hist = journal.load_price_history(
        conn, start=trade_date - timedelta(days=SCORE_LOOKBACK_DAYS), end=trade_date
    )
    if not hist:
        return StepResult("DailyScores", "journal", "failed",
                          error_message="DailyBars가 비어 있다 — 먼저 --backfill")

    panel = build_panel(hist, asof=trade_date)
    if panel.empty:
        return StepResult("DailyScores", "journal", "failed", len(hist), 0, 0,
                          "패널이 비었다(전 종목 워밍업 미완 의심)")

    passed = eligible(panel)
    weights = load_params("risk_params")["screener"]
    pool = panel.loc[panel.index.intersection(passed)]
    scores = screener.score(pool, weights) if not pool.empty else pd.Series(dtype=float)
    ranks = scores.rank(ascending=False, method="min")

    # 항목별 백분위 — 저장해두면 어느 항목이 점수를 끌었는지 사후에 볼 수 있다
    pct = {
        "momentum": _pct(pool, "momentum", True),
        "value": _pct(pool, "value", True),
        "lowvol": _pct(pool, "lowvol", False),
        "supply20": _pct(pool, "supply20", True),
    }

    rows = []
    for code, r in panel.iterrows():
        ok = code in passed
        rows.append({
            "symbol_id": code,
            "passed_filter": ok,
            "filter_reason": None if ok else _filter_reason(r),
            "momentum": _f(r.get("momentum")),
            "flow_net_20day": _f(r.get("supply20")),
            "value_ratio": _f(r.get("value")),
            "volatility": _f(r.get("lowvol")),
            "momentum_percentile": _f(pct["momentum"].get(code)),
            "flow_percentile": _f(pct["supply20"].get(code)),
            "value_percentile": _f(pct["value"].get(code)),
            "low_volatility_percentile": _f(pct["lowvol"].get(code)),
            "total_score": _f(scores.get(code)),
            "rank": None if code not in ranks.index or pd.isna(ranks[code])
                    else int(ranks[code]),
        })
    n = journal.upsert_daily_scores(conn, trade_date, rows)
    return StepResult("DailyScores", "journal", "ok", len(panel), len(passed), n)


def _pct(pool: pd.DataFrame, col: str, higher_better: bool) -> pd.Series:
    """통과 집합 안에서의 백분위. 컬럼이 없으면 빈 시리즈(저장 시 NULL)."""
    if pool.empty or col not in pool.columns:
        return pd.Series(dtype=float)
    return pool[col].rank(pct=True, ascending=higher_better)


def _filter_reason(row: pd.Series) -> str:
    """제외 사유 문자열을 만든다(겹치면 이어 붙임)."""
    from data.features import MIN_ADV20, MIN_PRICE

    reasons = []
    close, adv, mom = row.get("close"), row.get("adv20"), row.get("momentum")
    if close is None or pd.isna(close) or close < MIN_PRICE:
        reasons.append("동전주")
    if adv is None or pd.isna(adv) or adv < MIN_ADV20:
        reasons.append("거래대금미달")
    if mom is None or pd.isna(mom):
        reasons.append("워밍업미완")
    return ",".join(reasons) or "기타"


def _f(v) -> float | None:
    """NaN·None을 NULL로 바꾼다."""
    return None if v is None or pd.isna(v) else float(v)


# ── 배치 본체 ────────────────────────────────────────────────────
def main() -> None:
    """CLI 진입점 — ①종목명부 ②일봉·수급 ③지수 ④점수 순으로 배치를 실행한다."""
    ap = argparse.ArgumentParser(description="AlphaLoop 일일 배치")
    ap.add_argument("--date", help="대상 거래일(YYYY-MM-DD). 생략 시 직전 거래일")
    ap.add_argument("--backfill", type=int, default=0,
                    help="과거 N일치를 함께 받는다(최초 1회). 생략 시 대상일 하루만")
    ap.add_argument("--resume", action="store_true",
                    help="직전 partial을 이어받아 못 받은 종목만 재시도")
    ap.add_argument("--limit", type=int, default=0, help="조회 종목 수 제한(시험용)")
    ap.add_argument("--skip-symbols", action="store_true",
                    help="종목 명부 갱신 건너뛰기(마스터 다운로드가 느릴 때)")
    args = ap.parse_args()

    trade_date = (date.fromisoformat(args.date) if args.date
                  else previous_trading_day(kst_today() + timedelta(days=1)))
    if not is_trading_day(trade_date):
        raise SystemExit(f"{trade_date}는 거래일이 아니다 — 배치를 돌리지 않는다")

    mode = get_settings().trading_mode
    conn = init_db()
    client = KISClient(mode=mode)
    print(f"[{mode}] 일일 배치 · 거래일 {trade_date}"
          f"{f' · 백필 {args.backfill}일' if args.backfill else ''}")

    # ① 종목 명부
    if not args.skip_symbols:
        started = now_utc()
        res = ingest_symbols(conn)
        _record(conn, trade_date, started, res)
        print(f"  ① Symbols        {res.status:<8} {res.rows_written:,}행")
        if res.status == "failed":
            raise SystemExit(f"종목 명부 실패로 중단: {res.error_message}")

    codes = journal.load_symbol_ids(conn)
    if args.limit:
        codes = codes[:args.limit]
    if not codes:
        raise SystemExit("Symbols가 비어 있다 — --skip-symbols 없이 먼저 돌릴 것")

    # ② 일봉·수급
    start = trade_date - timedelta(days=args.backfill) if args.backfill else trade_date
    done = _already_done(conn, trade_date) if args.resume else set()
    if done:
        print(f"  이어받기: {len(done):,}종목 건너뜀")
    started = now_utc()
    bars, flows = ingest_bars_and_flows(
        conn, client, codes, start=start, end=trade_date, skip_done=done
    )
    _record(conn, trade_date, started, bars)
    _record(conn, trade_date, started, flows)
    for label, r in (("② DailyBars   ", bars), ("   DailyFlows ", flows)):
        print(f"  {label} {r.status:<8} {r.success_count:,}/{r.target_count:,}종목 "
              f"{r.rows_written:,}행")

    # ③ 지수
    started = now_utc()
    idx = ingest_indices(
        conn, start=trade_date - timedelta(days=INDEX_LOOKBACK_DAYS), end=trade_date
    )
    _record(conn, trade_date, started, idx)
    print(f"  ③ MarketIndices  {idx.status:<8} {idx.rows_written:,}행")

    # ④ 점수 — 앞 단계가 쌓아둔 DB를 읽는다
    started = now_utc()
    scores = compute_daily_scores(conn, trade_date=trade_date)
    _record(conn, trade_date, started, scores)
    print(f"  ④ DailyScores    {scores.status:<8} "
          f"{scores.success_count:,}/{scores.target_count:,}종목 통과 "
          f"{scores.rows_written:,}행")

    failed = [r for r in (bars, flows, idx, scores) if r.status == "failed"]
    if failed:
        raise SystemExit(f"실패 단계: {[r.target_table for r in failed]}")
    conn.close()


def _already_done(conn, trade_date: date) -> set[str]:
    """그날 일봉이 이미 들어간 종목코드 집합을 반환한다."""
    rows = conn.execute(
        'SELECT "SymbolId" FROM "DailyBars" WHERE "TradeDate"=%s', (trade_date,)
    ).fetchall()
    return {r["SymbolId"] for r in rows}


if __name__ == "__main__":
    main()
