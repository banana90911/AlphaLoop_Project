"""
description:        KIS 클라이언트 순수 로직 (네트워크 없이, 실호출은 별도)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import pytest

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
