"""
description:        전 종목 점수 계산의 종목 청크 분할 (10-ops 10.12)
author:             siheon jung
created date:       2026/09/12
remarks:            청크는 메모리 피크를 낮추는 장치이지 결과를 바꾸는 장치가 아니다.
                    순위·백분위는 횡단면 계산이라 청크 경계가 결과에 새면 점수가
                    조용히 틀어진다 — 그걸 막는 것이 이 파일의 목적이다.
"""

from datetime import date, timedelta

import pytest

import run_daily_ingest as batch
from memory import journal

_END = date(2026, 9, 11)
_DAYS = 290                  # 모멘텀 252거래일 + 20일 스킵 + 여유
_CODES = ["000100", "000200", "000300", "000400", "000500"]

# 비교할 칸 — 점수·순위·백분위가 핵심이고, 원시값도 같이 본다
_COLS = (
    "symbol_id", "trade_date", "passed_filter", "total_score", "rank",
    "momentum", "momentum_percentile", "flow_net_20_day", "flow_percentile",
    "value_ratio", "value_percentile", "volatility", "low_volatility_percentile",
    "filter_reason",
)


def _business_days(end: date, n: int) -> list[date]:
    """end에서 과거로 평일 n개를 오래된 순으로 반환한다."""
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


@pytest.fixture
def seeded(conn):
    """종목 5개 × 290거래일 일봉·수급. 종목마다 기울기를 달리 줘 순위가 갈리게 한다."""
    journal.upsert_symbols(
        conn, [{"code": c, "name": f"종목{c}", "market": "KOSPI"} for c in _CODES]
    )
    days = _business_days(_END, _DAYS)
    for k, code in enumerate(_CODES):
        bars, flows = [], []
        for i, d in enumerate(days):
            close = 10_000.0 + i * (k + 1) * 10
            # 거래대금(종가×거래량)이 제외 필터의 30억을 넘도록 잡는다 —
            # 안 넘으면 전 종목이 걸러져 순위·백분위가 전부 비고, 그러면
            # "청크로 나눠도 같다"가 빈 결과끼리의 비교가 되어 의미를 잃는다.
            bars.append({"date": d, "open": close, "high": close * 1.01,
                         "low": close * 0.99, "close": close,
                         "volume": 500_000 + i})
            flows.append({"date": d, "foreign_net": (k + 1) * 1_000.0,
                          "institution_net": -(k + 1) * 500.0,
                          "individual_net": 0.0})
        journal.upsert_daily_bars(conn, code, bars)
        journal.upsert_daily_flows(conn, code, flows)
    return conn


def _saved(conn) -> list[tuple]:
    """적재된 DailyScores를 비교 가능한 튜플 목록으로 꺼낸다."""
    rows = conn.execute(
        'SELECT * FROM daily_scores ORDER BY symbol_id'
    ).fetchall()
    return [tuple(r[c] for c in _COLS) for r in rows]


def _run(conn, monkeypatch, chunk: int):
    """청크 크기를 바꿔 점수 배치 ④단계만 돌리고, 적재 결과를 돌려준다."""
    conn.execute('DELETE FROM daily_scores')
    conn.commit()
    monkeypatch.setattr(batch, "SCORE_CHUNK_SYMBOLS", chunk)
    res = batch.compute_daily_scores(conn, trade_date=_END)
    return res, _saved(conn)


def test_청크로_나눠도_점수가_같다(seeded, monkeypatch):
    """청크 2개씩 vs 한 번에 — 적재된 점수·순위·백분위가 완전히 같아야 한다."""
    whole_res, whole = _run(seeded, monkeypatch, 1_000)     # 전 종목 한 청크
    chunked_res, chunked = _run(seeded, monkeypatch, 2)     # 5종목 → 3청크

    assert whole_res.status == "ok"
    assert chunked_res.status == "ok"
    assert len(whole) == len(_CODES)
    assert chunked == whole


def test_청크_경계가_종목을_빠뜨리지_않는다(seeded, monkeypatch):
    """청크 크기가 종목 수를 나누어떨어지지 않아도 전 종목이 들어간다."""
    _, rows = _run(seeded, monkeypatch, 3)                  # 5 = 3 + 2
    assert sorted(r[0] for r in rows) == _CODES


def test_한_종목씩_처리해도_순위는_횡단면으로_매겨진다(seeded, monkeypatch):
    """청크 1이어도 순위가 1..N으로 나와야 한다 — 청크 안에서 매기면 전부 1이 된다."""
    _, rows = _run(seeded, monkeypatch, 1)
    ranks = sorted(r[4] for r in rows if r[4] is not None)
    assert ranks == list(range(1, len(ranks) + 1))
    assert len(ranks) > 1                                   # 순위가 실제로 갈렸다


def test_일봉이_있는_종목만_청크_대상이_된다(seeded):
    """`load_bar_symbol_ids`는 Symbols가 아니라 DailyBars를 기준으로 한다."""
    journal.upsert_symbols(
        seeded, [{"code": "999999", "name": "일봉없음", "market": "KOSPI"}]
    )
    codes = journal.load_bar_symbol_ids(
        seeded, start=_END - timedelta(days=batch.SCORE_LOOKBACK_DAYS), end=_END
    )
    assert codes == _CODES                                  # 999999는 빠진다


def test_기준일은_기간_내_마지막_거래일(seeded):
    """점수 라벨이 되는 기준일 — 통짜로 읽을 때의 max와 같아야 한다."""
    last = journal.latest_bar_date(
        seeded, start=_END - timedelta(days=batch.SCORE_LOOKBACK_DAYS), end=_END
    )
    assert last == _END


def test_일봉이_없으면_실패로_남는다(conn):
    """DailyBars가 비면 점수 단계는 failed — 빈 패널로 조용히 넘어가지 않는다."""
    res = batch.compute_daily_scores(conn, trade_date=_END)
    assert res.status == "failed"
    assert "DailyBars" in (res.error_message or "")
