"""
description:        계좌 현금 = D+2 예수금 (05-risk 5.2 검사 1-b)
author:             siheon jung
created date:       2026/09/17
remarks:            2026-09-17 실계좌: 38,850원어치를 산 뒤에도 예수금총금액은 200,000원
                    그대로였다(결제 T+2). 그걸 현금으로 읽으면 산 주식이 현금과 보유에
                    두 번 잡혀 자본이 238,830원으로 부풀고, 잔차가 가짜 입금이 된다.
"""

from broker.kis_client import KISClient

# 그날 실제 응답 요약
_SUMMARY = {"dnca_tot_amt": "200000", "nxdy_excc_amt": "200000",
            "prvs_rcdl_excc_amt": "161150", "scts_evlu_amt": "38830",
            "tot_evlu_amt": "199980", "bfdy_tot_asst_evlu_amt": "200000"}


def _balance(monkeypatch, summary: dict):
    client = KISClient.__new__(KISClient)
    monkeypatch.setattr(client, "get_balance", lambda: {"output1": [], "output2": [summary]},
                        raising=False)
    return client.fetch_balance()


def test_현금은_D2_예수금이다(monkeypatch):
    assert _balance(monkeypatch, _SUMMARY).cash == 161_150


def test_현금과_보유를_더하면_KIS_총평가와_맞는다(monkeypatch):
    """결제 전 예수금으로 읽으면 238,830원이 되어 KIS 총평가(199,980원)와 어긋난다."""
    b = _balance(monkeypatch, _SUMMARY)
    assert b.cash + float(_SUMMARY["scts_evlu_amt"]) == b.total_asset == 199_980


def test_D2_칸이_없으면_예수금총금액으로_대신한다(monkeypatch):
    summary = {k: v for k, v in _SUMMARY.items() if k != "prvs_rcdl_excc_amt"}
    assert _balance(monkeypatch, summary).cash == 200_000


def test_D2_예수금이_진짜_0이면_0이다(monkeypatch):
    """0원을 '값 없음'으로 오인해 폴백하면 안 된다."""
    assert _balance(monkeypatch, {**_SUMMARY, "prvs_rcdl_excc_amt": "0"}).cash == 0
