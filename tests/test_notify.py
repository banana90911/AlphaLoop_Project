"""
description:        요약 알림 조립 검사 (10-ops 10.4)
author:             siheon jung
created date:       2026/09/08
remarks:            네트워크는 타지 않는다 — conftest가 notify.send를 막아 두므로
                    여기서는 send를 가로채 만들어진 본문·등급만 본다.
"""

from datetime import date

import pytest

from ops import notify


@pytest.fixture
def sent(monkeypatch):
    """send에 넘어간 인자를 모아 둔다."""
    box: list[dict] = []

    def fake(message, *, level="info", title=None):
        box.append({"message": message, "level": level, "title": title})
        return True

    monkeypatch.setattr(notify, "send", fake)
    return box


_DAY = date(2026, 9, 8)


def _steps(*statuses: str) -> list[notify.StepLine]:
    return [notify.StepLine(f"t{i}", s, 10, 10, 100) for i, s in enumerate(statuses)]


def test_배치_전부_성공이면_알림이_나간다(sent):
    """성공도 보낸다 — 조용한 성공은 '안 돈 것'과 구별되지 않는다."""
    notify.notify_ingest_summary(_DAY, _steps("ok", "ok"), mode="real")
    assert sent[0]["level"] == "info"
    assert sent[0]["title"] == "일일 배치 완료"
    assert "2026-09-08" in sent[0]["message"]


def test_배치_등급은_가장_나쁜_단계를_따른다(sent):
    notify.notify_ingest_summary(_DAY, _steps("ok", "partial", "ok"))
    notify.notify_ingest_summary(_DAY, _steps("ok", "partial", "failed"))
    assert (sent[0]["title"], sent[0]["level"]) == ("일일 배치 부분 성공", "warning")
    assert (sent[1]["title"], sent[1]["level"]) == ("일일 배치 실패", "critical")


def test_배치_실패면_재시도_방법을_함께_준다(sent):
    notify.notify_ingest_summary(_DAY, _steps("partial"))
    assert "--resume" in sent[0]["message"]


def test_배치_성공에는_재시도_안내를_붙이지_않는다(sent):
    notify.notify_ingest_summary(_DAY, _steps("ok"))
    assert "--resume" not in sent[0]["message"]


def _buy(code="005930", name="삼성전자", qty=3, price=71_000):
    return notify.TradeLine(code, name, "buy", "entry", qty, price, "filled")


def _sell(code="000660", name="SK하이닉스", qty=2, price=180_000):
    return notify.TradeLine(code, name, "sell", "exit", qty, price, "filled")


def _hold(code="005930", name="삼성전자", qty=3, avg=71_000, stop=67_000):
    return notify.HoldingLine(code, name, qty, avg, stop)


def test_사이클_정상은_매수_매도_보유를_모두_담는다(sent):
    notify.notify_cycle_summary(
        "C1", "recorded", action="proceed", watchlist=27, planned=2, live=True,
        trades=[_buy(), _sell()], holdings=[_hold()],
    )
    m = sent[0]["message"]
    assert sent[0]["title"] == "사이클 완료" and sent[0]["level"] == "info"
    assert "워치리스트 27종목" in m
    assert "삼성전자(005930) 매수 3주 @ 71,000원" in m
    assert "SK하이닉스(000660) 매도 2주 @ 180,000원" in m
    assert "**보유** 1종목" in m and "손절 67,000" in m
    assert "드라이런" not in m


def test_매매가_없으면_없다고_밝힌다(sent):
    """빈 줄로 두면 '알림이 잘린 것'과 구별되지 않는다."""
    notify.notify_cycle_summary(
        "C1", "recorded", action="proceed", watchlist=27, planned=0, live=True)
    m = sent[0]["message"]
    assert "**매수** 없음" in m and "**매도** 없음" in m and "**보유** 없음" in m


def test_손절_없는_보유가_있으면_경고로_올린다(sent):
    """손절 없는 보유는 밤사이 갭에 무방비다."""
    notify.notify_cycle_summary(
        "C1", "recorded", action="proceed", watchlist=1, planned=0, live=True,
        holdings=[_hold(stop=None)],
    )
    assert sent[0]["level"] == "warning"
    assert "손절 없음" in sent[0]["message"]


def test_미체결은_수량_대신_상태를_보여준다(sent):
    notify.notify_cycle_summary(
        "C1", "recorded", action="proceed", watchlist=1, planned=1, live=True,
        trades=[notify.TradeLine("005930", "삼성전자", "buy", "entry", 0, None,
                                 "rejected")],
    )
    assert "미체결(`rejected`)" in sent[0]["message"]


def test_드라이런이면_주문을_안_냈다고_밝힌다(sent):
    notify.notify_cycle_summary(
        "C1", "recorded", action="proceed", watchlist=5, planned=1, live=False)
    assert "드라이런" in sent[0]["message"]


def test_사이클_건너뜀은_사유와_함께_경고로_나간다(sent):
    notify.notify_cycle_summary(
        "C1", "skipped", action="skip", watchlist=0, planned=0, live=True,
        reason="noCandidates",
    )
    assert sent[0]["title"] == "사이클 건너뜀" and sent[0]["level"] == "warning"
    assert "noCandidates" in sent[0]["message"]


def test_손절_체결은_실현손익을_담는다(sent):
    notify.notify_stop_filled(
        "005930", quantity=3, entry=71_000, exit_price=67_000, net=-12_500,
        return_percent=-0.0587, name="삼성전자")
    m = sent[0]["message"]
    assert sent[0]["title"] == "손절 체결" and sent[0]["level"] == "warning"
    assert "매수 71,000원 → 매도 67,000원" in m
    assert "−12,500원" in m and "-5.87%" in m


def test_감시_이상_없으면_정보_등급(sent):
    notify.notify_watch_summary(positions=3, missing=0, registered=0)
    assert sent[0]["level"] == "info"
    assert "보유 3종목" in sent[0]["message"]
    assert "정정할 것 없음" in sent[0]["message"]


def test_감시_트레일링_정정_내역을_보여준다(sent):
    notify.notify_watch_summary(
        positions=1, missing=0, registered=0,
        stale=["005930 67,000 → 69,000"], revised=["005930"])
    m = sent[0]["message"]
    assert sent[0]["level"] == "info"
    assert "② 손절선 정정 1/1건" in m and "005930 67,000 → 69,000" in m


def test_감시_정정_실패가_남으면_경고로_올린다(sent):
    notify.notify_watch_summary(
        positions=2, missing=0, registered=0,
        stale=["005930 67,000 → 69,000", "000660 170,000 → 175,000"],
        revised=["005930"])
    assert sent[0]["level"] == "warning"
    assert "일부가 정정되지 않았습니다" in sent[0]["message"]


def test_감시_손절_구멍은_즉시_확인_등급(sent):
    notify.notify_watch_summary(
        positions=1, missing=0, registered=0, gaps=["005930 현재가 1 ≤ 손절 2"])
    assert sent[0]["level"] == "critical"


def test_감시_스톱_빠짐은_경고_등급(sent):
    notify.notify_watch_summary(positions=2, missing=1, registered=1)
    assert sent[0]["level"] == "warning"
    assert "등록 1건" in sent[0]["message"]
