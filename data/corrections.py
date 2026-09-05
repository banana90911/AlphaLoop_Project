"""
description:        수정주가·신선도·이상치 방어 (데이터 검증 계층)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date, timedelta

import pandas as pd

from core.timeutils import now_utc

# 한국 주식 가격제한폭. 이보다 큰 하루 변동은 기업행위 미조정이나 데이터 오류다.
PRICE_LIMIT = 0.30
# 배치가 이보다 오래됐으면 낡은 것으로 본다(장 시작 전 배치 → 14:30 사이클까지 여유).
DEFAULT_MAX_AGE_HOURS = 12.0


# ── ① 수정주가 ───────────────────────────────────────────────────
def apply_adjustments(bars: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """기업행위 기준일·배수로 과거 시계열을 수정주가로 되돌린다."""
    if bars.empty or actions is None or actions.empty:
        out = bars.copy()
        out["adjustment_factor"] = 1.0
        out["is_adjusted"] = False
        return out

    out = bars.copy().sort_index()
    factor = pd.Series(1.0, index=out.index)
    for a in actions.sort_values("ex_date", ascending=False).itertuples():
        pf = getattr(a, "price_factor", None)
        if pf is None or pd.isna(pf) or pf <= 0:
            continue                        # 배수를 모르면 건드리지 않는다(유상증자 등)
        factor.loc[factor.index < a.ex_date] *= float(pf)

    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col] * factor
    if "volume" in out.columns:             # 가격이 내려간 만큼 수량은 늘어난다
        out["volume"] = out["volume"] / factor
    out["adjustment_factor"] = factor
    out["is_adjusted"] = factor != 1.0
    return out


def adjust_stop_for_action(stop_price: float, price_factor: float) -> float:
    """권리락 당일 보유 종목의 손절선을 같은 비율로 내린다."""
    if price_factor is None or price_factor <= 0:
        return stop_price
    return stop_price * price_factor


# ── ② 신선도 ─────────────────────────────────────────────────────
def check_freshness(
    conn, *, trade_date: date, tables: tuple[str, ...] = ("daily_bars", "daily_scores"),
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> tuple[bool, str]:
    """오늘 배치가 제때 `ok`로 끝났는지 확인한다. 반환: (통과 여부, 사유)."""
    from memory import journal

    now = now_utc()
    for table in tables:
        run = journal.last_ingest_run(conn, table, trade_date)
        if run is None:
            return False, f"{table} 배치 기록 없음({trade_date})"
        if run["status"] != "ok":
            return False, f"{table} 배치 {run['status']}: {run['error_message'] or '사유 미기록'}"
        finished = run["finished_date_time"]
        if finished is None:
            return False, f"{table} 배치가 끝나지 않았다"
        age_h = (now - finished).total_seconds() / 3600
        if age_h > max_age_hours:
            return False, f"{table} 배치가 {age_h:.1f}시간 전 — {max_age_hours}시간 초과"
    return True, ""


# ── ③ 이상치 방어 ────────────────────────────────────────────────
def flag_price_jumps(bars: pd.DataFrame, *, limit: float = PRICE_LIMIT) -> pd.Series:
    """가격제한폭(±30%)을 넘는 하루 점프에 True를 표시한다."""
    if bars.empty or "close" not in bars.columns:
        return pd.Series(dtype=bool)
    ret = bars["close"].pct_change()
    return (ret.abs() > limit).fillna(False)


def drop_stale_rows(bars: pd.DataFrame, *, asof: date, max_gap_days: int = 10
                    ) -> pd.DataFrame:
    """마지막 거래일이 asof에서 너무 멀면 통째로 버린다(거래정지·상장폐지 종목)."""
    if bars.empty:
        return bars
    last = bars.index.max()
    last_date = last.date() if hasattr(last, "date") else last
    if (asof - last_date) > timedelta(days=max_gap_days):
        return bars.iloc[0:0]
    return bars
