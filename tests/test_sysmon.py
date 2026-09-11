"""
description:        서버 자원 감시 — 임계 판정 (10-ops 10.4·10.10)
author:             siheon jung
created date:       2026/09/11
remarks:            조용히 죽는 것을 막는 장치다. 감시가 예외를 내서 매매를 멈추면
                    본말이 전도되므로, 측정 실패는 알림이지 예외가 아니다.
"""

from collections import namedtuple

import pytest

from ops import sysmon

_Usage = namedtuple("_Usage", "total used free")
_GB = 1024 ** 3


def _disk(monkeypatch, used_pct: float):
    total = 100 * _GB
    used = int(total * used_pct / 100)
    monkeypatch.setattr(sysmon.shutil, "disk_usage",
                        lambda p: _Usage(total, used, total - used))
    return sysmon.check_disk()


def _meminfo(ram_mb: int, swap_total_mb: int, swap_free_mb: int) -> str:
    return (f"MemTotal: {ram_mb * 1024} kB\n"
            f"SwapTotal: {swap_total_mb * 1024} kB\n"
            f"SwapFree: {swap_free_mb * 1024} kB\n")


# ── 디스크 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("pct,level", [(50, "info"), (79, "info"),
                                       (80, "warning"), (89, "warning"),
                                       (90, "critical"), (99, "critical")])
def test_disk_thresholds(monkeypatch, pct, level):
    """설계가 정한 선은 80%다(10.10). 90%는 '곧 멈춘다'라 등급을 올린다."""
    assert _disk(monkeypatch, pct).level == level


def test_disk_reports_free_space(monkeypatch):
    """몇 %인지만으로는 손쓸 시간이 있는지 모른다 — 남은 용량을 같이 준다."""
    assert "20.0GB" in _disk(monkeypatch, 80).line


# ── 스왑 ────────────────────────────────────────────────────────
def test_swap_small_use_is_not_an_alert():
    """리눅스는 평소에도 몇 MB를 밀어낸다 — 그걸로 깨우면 알림이 소음이 된다."""
    mi = sysmon.read_meminfo(_meminfo(ram_mb=961, swap_total_mb=2047, swap_free_mb=2000))
    assert sysmon.check_swap(mi).level == "info"


def test_swap_over_ten_percent_of_ram_warns():
    """2026-09-11 실측: 961MB 램에 스왑 158MB — 배치가 램을 넘겨 쓰고 있었다."""
    mi = sysmon.read_meminfo(_meminfo(ram_mb=961, swap_total_mb=2047, swap_free_mb=2047 - 158))
    r = sysmon.check_swap(mi)
    assert r.level == "warning" and "158MB" in r.line


def test_swap_without_meminfo_is_not_an_error():
    """측정할 수 없는 것과 문제가 있는 것은 다르다."""
    assert sysmon.check_swap({}).level == "info"


def test_read_meminfo_ignores_non_numeric_lines():
    mi = sysmon.read_meminfo("MemTotal: 100 kB\nHugePages_Total: 0\nBogus:\n")
    assert mi["MemTotal"] == 100 * 1024


# ── 종합 ────────────────────────────────────────────────────────
def test_main_notifies_only_when_something_is_wrong(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(sysmon.notify, "notify_system_health",
                        lambda rs, *, healthy: sent.append({"healthy": healthy}) or True)

    ok = [sysmon.Reading("디스크", "info", "50%"), sysmon.Reading("스왑", "info", "0MB")]
    monkeypatch.setattr(sysmon, "collect", lambda: ok)
    assert sysmon.main([]) == 0 and sent == []

    bad = [*ok, sysmon.Reading("DB 연결", "critical", "끊김")]
    monkeypatch.setattr(sysmon, "collect", lambda: bad)
    assert sysmon.main([]) == 1
    assert sent == [{"healthy": False}]


def test_always_notify_sends_even_when_healthy(monkeypatch):
    """설치 직후 '알림 배선이 살아 있나'를 확인할 길이 있어야 한다."""
    sent: list[dict] = []
    monkeypatch.setattr(sysmon.notify, "notify_system_health",
                        lambda rs, *, healthy: sent.append({"healthy": healthy}) or True)
    monkeypatch.setattr(sysmon, "collect",
                        lambda: [sysmon.Reading("디스크", "info", "50%")])
    assert sysmon.main(["--always-notify"]) == 0
    assert sent == [{"healthy": True}]


def test_db_failure_is_reported_not_raised(monkeypatch):
    """감시가 예외를 던지면 그 자체가 조용한 실패가 된다."""
    import memory.db
    monkeypatch.setattr(memory.db, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("끊김")))
    r = sysmon.check_db()
    assert r.level == "critical" and "끊김" in r.line
