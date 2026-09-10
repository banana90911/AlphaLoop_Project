"""
description:        KIS 클라이언트 순수 로직 (네트워크 없이, 실호출은 별도)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import pytest
import requests

from broker import kis_client
from broker.kis_client import KISClient, KISError
from config.settings import Settings

# 더미 키(.env 무시) — extra='ignore'라 임의 필드는 무시됨
_PAPER = Settings(
    kis_paper_app_key="pk",
    kis_paper_app_secret="ps",
    kis_paper_account_no="50192225-01",
    trading_mode="paper",
    _env_file=None,
)
_REAL = Settings(
    kis_app_key="rk",
    kis_app_secret="rs",
    kis_account_no="47240999-01",
    trading_mode="real",
    _env_file=None,
)


def test_paper_profile_selected():
    c = KISClient(settings=_PAPER)
    assert c.mode == "paper"
    assert "openapivts" in c._profile["domain"]
    assert c._profile["tr"]["balance"] == "VTTC8434R"
    assert c._profile["tr"]["buy"] == "VTTC0802U"


def test_real_profile_selected():
    c = KISClient(settings=_REAL)
    assert "openapi.koreainvestment" in c._profile["domain"]
    assert c._profile["tr"]["balance"] == "TTTC8434R"


def test_account_parsed():
    c = KISClient(settings=_PAPER)
    assert c.cano == "50192225"
    assert c.acnt_prdt == "01"


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        KISClient(mode="bogus", settings=_PAPER)


def test_missing_key_raises():
    blank = Settings(trading_mode="paper", _env_file=None)
    with pytest.raises(KISError):
        KISClient(settings=blank)


def test_order_side_validated():
    c = KISClient(settings=_PAPER)
    with pytest.raises(ValueError):
        c.order_cash("005930", 1, 1000, side="hold")


# ── 잔고 정규화 — 0원과 '값 없음'을 가른다 ──────────────────────────────
def _balance(monkeypatch, summary: dict, holdings=()):
    c = KISClient(settings=_REAL)
    monkeypatch.setattr(
        KISClient, "get_balance",
        lambda self: {"output1": list(holdings), "output2": [summary]},
    )
    return c.fetch_balance()


def test_yesterday_zero_is_a_real_zero_not_a_missing_value(monkeypatch):
    """계좌가 정말 비어 있던 날 KIS는 전일 총자산을 '0'으로 정확히 준다.

    그걸 '값 없음'으로 읽고 오늘 총자산으로 폴백하면, 기준선에 오늘 입금액이
    이미 들어 있는데 손익률 계산에서 그 흐름을 또 더해 반토막이 난다
    (2026-09-10 실측: +13,694원 입금이 당일 −50%로 찍혔다).
    """
    b = _balance(monkeypatch, {
        "dnca_tot_amt": "13694", "tot_evlu_amt": "13694",
        "bfdy_tot_asst_evlu_amt": "0",
    })
    assert b.base_asset == 0.0          # 폴백하면 13694가 된다
    assert b.total_asset == 13694.0


def test_missing_yesterday_field_falls_back_to_today(monkeypatch):
    """계좌 개설 첫날처럼 필드 자체가 없을 때만 오늘 값으로 대신한다."""
    b = _balance(monkeypatch, {"dnca_tot_amt": "1000", "tot_evlu_amt": "1000"})
    assert b.base_asset == 1000.0


def test_empty_account_stays_zero(monkeypatch):
    """전부 0인 계좌가 어떤 값도 만들어내지 않아야 한다."""
    b = _balance(monkeypatch, {
        "dnca_tot_amt": "0", "tot_evlu_amt": "0", "bfdy_tot_asst_evlu_amt": "0",
    })
    assert (b.cash, b.total_asset, b.base_asset) == (0.0, 0.0, 0.0)


# ── 연결 실패 재시도 ────────────────────────────────────────────────────
def test_connection_error_is_retried(monkeypatch):
    """연결이 끊기면 상태 코드가 없어 5xx 판정에 걸리지 못한다.

    성격은 5xx와 같은 일시적 장애인데 재시도 없이 실패로 기록됐고, 전 종목 배치에서
    2,535번 중 한 번만 끊겨도 그날이 partial이 되어 사이클이 멈췄다
    (2026-09-09·09-10 이틀 연속, 매번 다른 종목).
    """
    calls = {"n": 0}

    class _Resp:
        status_code = 200
        def json(self):
            return {"rt_cd": "0", "output": {"ok": 1}}

    def fake_get(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("연결이 끊겼다")
        return _Resp()

    monkeypatch.setattr(kis_client.requests, "get", fake_get)
    monkeypatch.setattr(kis_client.time, "sleep", lambda s: None)
    c = KISClient(settings=_REAL)
    monkeypatch.setattr(KISClient, "_headers", lambda self, tr: {})
    monkeypatch.setattr(KISClient, "_throttle", lambda self: None)

    out = c._get("https://x", "/p", "TR", {})
    assert out["output"] == {"ok": 1}
    assert calls["n"] == 3               # 두 번 끊기고 세 번째에 성공


def test_connection_error_gives_up_with_a_clear_message(monkeypatch):
    """계속 끊기면 재시도를 소진하고 KISError로 올린다 — 종목 하나만 실패시킨다."""
    def always_fail(*a, **k):
        raise requests.ConnectionError("계속 끊긴다")

    monkeypatch.setattr(kis_client.requests, "get", always_fail)
    monkeypatch.setattr(kis_client.time, "sleep", lambda s: None)
    c = KISClient(settings=_REAL)
    monkeypatch.setattr(KISClient, "_headers", lambda self, tr: {})
    monkeypatch.setattr(KISClient, "_throttle", lambda self: None)

    with pytest.raises(KISError, match="연결 실패"):
        c._get("https://x", "/p", "TR", {})
