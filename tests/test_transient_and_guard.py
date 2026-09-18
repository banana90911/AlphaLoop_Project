"""
description:        KIS 일시적 오류 재시도 + 진입점 최상위 예외 처리
author:             siheon jung
created date:       2026/09/15
remarks:            2026-09-15 실거래 첫날 실패 재현. 잔고조회가 "초당 거래건수 초과"로
                    죽으면서 사이클이 시작도 못 했고, Cycles 행 이전이라 알림도 안 갔다.
                    두 구멍을 한 파일에서 막는다.
"""

import pytest
import requests

from broker.kis_client import KISClient, KISError, KISTransientError
from ops.guard import guard

# 그날 실제로 돌아온 응답
_RATE_LIMIT = {"rt_cd": "1", "msg_cd": "EGW00201",
               "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다."}
# 2026-09-18에 온 것은 코드가 달랐다(EGW00215). 문구로도 걸러야 하는 이유다.
_RATE_LIMIT_215 = {**_RATE_LIMIT, "msg_cd": "EGW00215"}
_NO_CASH = {"rt_cd": "1", "msg_cd": "40240000", "msg1": "주문가능금액이 부족합니다."}


class _Resp:
    """requests.Response 흉내 — 상태코드와 JSON 본문만 쓴다."""

    def __init__(self, body: dict, status: int = 200) -> None:
        self._body, self.status_code, self.text = body, status, str(body)

    def json(self) -> dict:
        return self._body


# ── 일시적 오류 판별 ────────────────────────────────────────────────────────

def test_초당_제한은_일시적_오류로_분류된다():
    with pytest.raises(KISTransientError):
        KISClient._unwrap(_Resp(_RATE_LIMIT), "TTTC8434R")


def test_잔고부족은_일시적이_아니다():
    """다시 보내도 같은 답이 온다 — 재시도하면 안 된다."""
    with pytest.raises(KISError) as e:
        KISClient._unwrap(_Resp(_NO_CASH), "TTTC0802U")
    assert not isinstance(e.value, KISTransientError)


def test_일시적_오류도_KISError로_잡힌다():
    """기존 `except KISError` 핸들러가 그대로 동작해야 한다."""
    assert issubclass(KISTransientError, KISError)


# ── 재시도 ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    """네트워크·설정 없이 _get만 돌릴 수 있는 최소 클라이언트."""
    c = KISClient.__new__(KISClient)
    c._min_interval, c._last_call = 0.0, 0.0
    c._max_retries, c._backoff_base = 3, 0.0     # 테스트는 기다리지 않는다
    c._transient_attempts, c._transient_base, c._transient_jitter = 6, 0.0, 0.0
    monkeypatch.setattr(KISClient, "_headers", lambda self, tr: {})
    return c


def _responses(monkeypatch, *bodies):
    """requests.get이 순서대로 돌려줄 응답을 깔아두고 호출 횟수를 센다."""
    calls = {"n": 0}

    def fake_get(url, **kw):
        i = calls["n"]
        calls["n"] += 1
        return _Resp(bodies[min(i, len(bodies) - 1)])

    monkeypatch.setattr("broker.kis_client.requests.get", fake_get)
    return calls


def test_초당_제한은_재시도해서_살아난다(client, monkeypatch):
    """이것이 없어서 그날 사이클이 통째로 죽었다."""
    calls = _responses(monkeypatch, _RATE_LIMIT, {"rt_cd": "0", "output": "ok"})
    body = client._get("https://x", "/p", "TTTC8434R", {})
    assert body["output"] == "ok"
    assert calls["n"] == 2                      # 한 번 실패하고 다시 걸었다


def test_계속_제한이면_결국_올린다(client, monkeypatch):
    calls = _responses(monkeypatch, _RATE_LIMIT)
    with pytest.raises(KISTransientError):
        client._get("https://x", "/p", "TTTC8434R", {})
    assert calls["n"] == 6                      # 일시적 오류 전용 횟수만큼


def test_초당_제한은_일반_재시도보다_오래_버틴다(client, monkeypatch):
    """1~2초 재시도 3번은 제한 구간 안에 다 들어간다 — 2026-09-15·09-18 사이클 사망."""
    calls = _responses(monkeypatch, *([_RATE_LIMIT_215] * 4), {"rt_cd": "0", "output": "ok"})
    assert client._get("https://x", "/p", "TTTC8434R", {})["output"] == "ok"
    assert calls["n"] == 5                      # 3회에서 포기하지 않는다


def test_다른_코드로_와도_문구로_걸러진다(client, monkeypatch):
    calls = _responses(monkeypatch, _RATE_LIMIT_215, {"rt_cd": "0", "output": "ok"})
    assert client._get("https://x", "/p", "TTTC8434R", {})["output"] == "ok"
    assert calls["n"] == 2


def test_대기는_점점_길어지고_지터가_붙는다(client, monkeypatch):
    """같은 초에 여러 프로세스가 몰리면 다 같이 다시 막힌다."""
    client._transient_base, client._transient_jitter = 2.0, 0.5
    waits: list[float] = []
    monkeypatch.setattr("broker.kis_client.time.sleep", waits.append)
    _responses(monkeypatch, *([_RATE_LIMIT] * 3), {"rt_cd": "0", "output": "ok"})
    client._get("https://x", "/p", "TTTC8434R", {})
    assert len(waits) == 3
    assert 2.0 <= waits[0] < 2.5 and 4.0 <= waits[1] < 4.5 and 8.0 <= waits[2] < 8.5


def test_연결_실패는_기존_횟수를_지킨다(client, monkeypatch):
    """일시적 오류만 오래 버틴다 — 연결 실패까지 1분을 끌면 배치가 늘어진다."""
    calls = {"n": 0}

    def boom(url, **kw):
        calls["n"] += 1
        raise requests.ConnectionError("끊김")

    monkeypatch.setattr("broker.kis_client.requests.get", boom)
    with pytest.raises(KISError):
        client._get("https://x", "/p", "TTTC8434R", {})
    assert calls["n"] == 3                      # max_retries


def test_영구_오류는_재시도하지_않는다(client, monkeypatch):
    """잔고 부족으로 3번 더 두드리면 한도만 갉아먹는다."""
    calls = _responses(monkeypatch, _NO_CASH)
    with pytest.raises(KISError):
        client._get("https://x", "/p", "TTTC8434R", {})
    assert calls["n"] == 1


# ── 진입점 최상위 그물 ──────────────────────────────────────────────────────

def test_기록_전에_죽어도_알림이_간다(monkeypatch):
    """Cycles 행이 생기기 전에 죽는 구간을 덮는 것이 guard의 목적이다."""
    sent, pinged = [], []
    monkeypatch.setattr("ops.notify.notify_entrypoint_crash",
                        lambda *a: sent.append(a) or True)
    monkeypatch.setattr("ops.heartbeat.ping_failure", lambda d: pinged.append(d) or True)

    with pytest.raises(KISTransientError):
        with guard("run_cycle"):
            raise KISTransientError("TTTC8434R rt_cd=1 msg=초당 거래건수 초과")

    assert sent and sent[0][0] == "run_cycle"
    assert "초당" in sent[0][1]
    assert pinged


def test_정상_종료는_알리지_않는다(monkeypatch):
    """거래일이 아니라 스스로 끝내는 경로까지 경보가 울리면 소음이 된다."""
    sent = []
    monkeypatch.setattr("ops.notify.notify_entrypoint_crash",
                        lambda *a: sent.append(a) or True)
    with pytest.raises(SystemExit):
        with guard("run_cycle"):
            raise SystemExit("2026-09-15는 거래일이 아니다")
    assert sent == []


def test_알림이_실패해도_원래_예외가_보존된다(monkeypatch):
    """진단은 traceback이 우선이다 — 알림 오류가 원인을 덮으면 안 된다."""
    def boom(*a):
        raise RuntimeError("웹훅 죽음")

    monkeypatch.setattr("ops.notify.notify_entrypoint_crash", boom)
    with pytest.raises(ValueError, match="진짜 원인"):
        with guard("run_cycle"):
            raise ValueError("진짜 원인")
