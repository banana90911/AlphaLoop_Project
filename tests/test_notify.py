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


def test_사이클_정상은_계획_건수를_담는다(sent):
    notify.notify_cycle_summary(
        "C1", "recorded", action="proceed", watchlist=27, planned=2,
        submitted=2, live=True,
    )
    assert sent[0]["title"] == "사이클 완료" and sent[0]["level"] == "info"
    assert "워치리스트 27종목" in sent[0]["message"]
    assert "드라이런" not in sent[0]["message"]


def test_드라이런이면_주문을_안_냈다고_밝힌다(sent):
    notify.notify_cycle_summary(
        "C1", "recorded", action="proceed", watchlist=5, planned=1,
        submitted=0, live=False,
    )
    assert "드라이런" in sent[0]["message"]


def test_사이클_건너뜀은_사유와_함께_경고로_나간다(sent):
    notify.notify_cycle_summary(
        "C1", "skipped", action="skip", watchlist=0, planned=0, submitted=0,
        live=True, reason="noCandidates",
    )
    assert sent[0]["title"] == "사이클 건너뜀" and sent[0]["level"] == "warning"
    assert "noCandidates" in sent[0]["message"]


def test_감시_이상_없으면_정보_등급(sent):
    notify.notify_watch_summary(
        positions=3, missing=0, registered=0, stale=0, revised=0)
    assert sent[0]["level"] == "info"
    assert "보유 3종목" in sent[0]["message"]


def test_감시_손절_구멍은_즉시_확인_등급(sent):
    notify.notify_watch_summary(
        positions=1, missing=0, registered=0, stale=0, revised=0,
        gaps=["005930 현재가 1 ≤ 손절 2"])
    assert sent[0]["level"] == "critical"


def test_감시_스톱_빠짐은_경고_등급(sent):
    notify.notify_watch_summary(
        positions=2, missing=1, registered=1, stale=0, revised=0)
    assert sent[0]["level"] == "warning"
    assert "등록 1건" in sent[0]["message"]
