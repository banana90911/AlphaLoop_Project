"""
description:        일일 배치 — 일시적 실패의 같은-실행 재시도 (10-ops 10.3)
author:             siheon jung
created date:       2026/09/10
remarks:            전 종목 배치는 2,500번 넘게 호출하므로 그중 한 번만 끊겨도
                    그날이 partial이 되고 신선도 검사가 사이클을 통째로 멈춘다.
"""

from datetime import date

import pandas as pd
import pytest

import run_daily_ingest as batch
from memory import journal

_START, _END = date(2026, 9, 9), date(2026, 9, 10)
_CODES = ["000100", "000200", "000300"]


@pytest.fixture
def seeded(conn):
    journal.upsert_symbols(
        conn, [{"code": c, "name": f"종목{c}", "market": "KOSPI"} for c in _CODES]
    )
    return conn


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {"date": [_END], "open": [100.0], "high": [110.0], "low": [90.0],
         "close": [105.0], "volume": [1000.0]}
    )


def _flows() -> list[dict]:
    return [{"date": _END, "foreign_net": 1.0, "institution_net": 2.0,
             "individual_net": -3.0}]


def _run(conn, monkeypatch, *, bars_fail: dict, flows_fail: dict):
    """bars_fail/flows_fail = {종목코드: 남은 실패 횟수}"""
    def fake_bars(client, code, s, e):
        if bars_fail.get(code, 0) > 0:
            bars_fail[code] -= 1
            raise ConnectionError("연결이 끊겼다")
        return _bars()

    def fake_flows(client, code):
        if flows_fail.get(code, 0) > 0:
            flows_fail[code] -= 1
            raise ConnectionError("연결이 끊겼다")
        return _flows()

    monkeypatch.setattr(batch.kis_history, "fetch_ohlcv_range", fake_bars)
    monkeypatch.setattr(batch, "_recent_flows", fake_flows)
    return batch.ingest_bars_and_flows(
        conn, None, _CODES, start=_START, end=_END
    )


def test_전부_성공하면_ok(seeded, monkeypatch):
    bars, flows = _run(seeded, monkeypatch, bars_fail={}, flows_fail={})
    assert bars.status == "ok" and bars.success_count == 3
    assert flows.status == "ok" and flows.success_count == 3


def test_일시적_일봉_실패는_재시도로_회복된다(seeded, monkeypatch):
    """한 번 끊긴 종목은 같은 실행 안에서 다시 받아 ok에 도달해야 한다."""
    bars, flows = _run(seeded, monkeypatch, bars_fail={"000200": 1}, flows_fail={})
    assert bars.status == "ok" and bars.error_message is None
    assert flows.status == "ok"
    assert bars.success_count == 3 and flows.success_count == 3


def test_일시적_수급_실패는_수급만_다시_받는다(seeded, monkeypatch):
    """일봉은 이미 성공했으므로 두 번 세면 안 된다(3종목인데 4가 되면 버그)."""
    bars, flows = _run(seeded, monkeypatch, bars_fail={}, flows_fail={"000300": 1})
    assert bars.success_count == 3          # 재시도가 일봉을 다시 세지 않았다
    assert flows.status == "ok" and flows.success_count == 3


def test_계속_실패하면_partial로_남고_종목이_찍힌다(seeded, monkeypatch):
    """진짜 장애는 재시도로도 안 붙는다 — 그건 partial이 맞다."""
    bars, flows = _run(seeded, monkeypatch, bars_fail={"000200": 9}, flows_fail={})
    assert bars.status == "partial" and bars.success_count == 2
    assert "000200 ConnectionError" in bars.error_message
    # 일봉을 못 받았으면 수급도 못 받는다
    assert flows.status == "partial" and flows.success_count == 2


def test_수급_오류가_일봉_행에_새지_않는다(seeded, monkeypatch):
    """두 단계는 각자의 행으로 기록된다 — 성공한 단계가 실패로 보이면 안 된다."""
    bars, flows = _run(seeded, monkeypatch, bars_fail={}, flows_fail={"000300": 9})
    assert bars.status == "ok" and bars.error_message is None
    assert flows.status == "partial" and "000300" in flows.error_message
