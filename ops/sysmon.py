"""
description:        서버 자원 감시 (디스크·스왑·DB 연결)
author:             siheon jung
created date:       2026/09/11
remarks:            10-ops 10.4(스왑·DB 연결)·10.10(디스크 80%)이 요구하는 조기 경보.
                    조용히 죽는 것을 막는 장치라 매매 경로와 분리해 둔다 — 여기서
                    예외가 나도 매매는 영향을 받지 않아야 한다.
"""

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

from ops import notify

# 디스크: 설계가 정한 선(10.10)은 80%다. 90%는 "곧 멈춘다"는 뜻이라 등급을 올린다.
DISK_WARN_PCT = 80.0
DISK_CRIT_PCT = 90.0
# 스왑: 몇 MB는 리눅스가 평소에도 밀어낸다. 램의 이 비율을 넘으면 실제로 부족한 것이다
# (2026-09-11 실측: 961MB 램에 스왑 158MB — 배치가 램을 넘겨 쓰고 있다는 신호).
SWAP_WARN_RATIO = 0.10

_MEMINFO = Path("/proc/meminfo")


@dataclass
class Reading:
    """한 번 측정한 값. 사람이 읽을 문장과 심각도를 스스로 안다."""
    name: str
    level: str          # info / warning / critical
    line: str

    @property
    def bad(self) -> bool:
        return self.level != "info"


def _gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f}GB"


def check_disk(path: str = "/") -> Reading:
    """루트 파티션 사용률. 디스크가 차면 DB도 로그도 백업도 같이 멈춘다."""
    u = shutil.disk_usage(path)
    pct = u.used / u.total * 100 if u.total else 0.0
    level = ("critical" if pct >= DISK_CRIT_PCT
             else "warning" if pct >= DISK_WARN_PCT else "info")
    return Reading("디스크", level,
                   f"{pct:.0f}% 사용 · 남은 공간 {_gb(u.free)} / 전체 {_gb(u.total)}")


def read_meminfo(text: str | None = None) -> dict[str, int]:
    """`/proc/meminfo`를 {키: 바이트}로. 인자를 주면 그 내용을 읽는다(테스트용)."""
    if text is None:
        text = _MEMINFO.read_text() if _MEMINFO.exists() else ""
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[key] = int(parts[0]) * 1024        # meminfo 단위는 kB
    return out


def check_swap(meminfo: dict[str, int] | None = None) -> Reading:
    """스왑 사용량. 스왑을 쓴다는 건 램이 모자라 디스크로 밀어냈다는 뜻이고,
    그 순간 모든 것이 수십 배 느려진다 — 대시보드 502의 원인이 이것이었다(10.12)."""
    mi = meminfo if meminfo is not None else read_meminfo()
    total, free = mi.get("SwapTotal", 0), mi.get("SwapFree", 0)
    ram = mi.get("MemTotal", 0)
    used = max(0, total - free)
    if not ram:
        return Reading("스왑", "info", "측정 불가(meminfo 없음)")
    ratio = used / ram
    level = "warning" if ratio >= SWAP_WARN_RATIO else "info"
    return Reading("스왑", level,
                   f"{used / 1024 ** 2:,.0f}MB 사용 (램 {ram / 1024 ** 2:,.0f}MB의 {ratio:.0%})")


def check_db() -> Reading:
    """DB가 실제로 응답하는지. 안 되면 배치도 사이클도 아무것도 못 한다."""
    try:
        from config.settings import get_settings
        from memory.db import connect
        conn = connect(get_settings().db_dsn_readonly or get_settings().db_dsn)
        conn.execute("SELECT 1").fetchone()
        return Reading("DB 연결", "info", "정상")
    except Exception as e:                       # 감시가 매매를 멈추면 안 된다
        return Reading("DB 연결", "critical", f"{type(e).__name__}: {e}")


def collect() -> list[Reading]:
    return [check_disk(), check_swap(), check_db()]


def main(argv: list[str] | None = None) -> int:
    """CLI — 이상이 있을 때만 알린다. 반환: 0 정상, 1 이상 발견."""
    ap = argparse.ArgumentParser(description="AlphaLoop 서버 자원 감시")
    ap.add_argument("--always-notify", action="store_true",
                    help="이상이 없어도 알림을 보낸다(설치 직후 배선 확인용)")
    args = ap.parse_args(argv)

    readings = collect()
    for r in readings:
        mark = {"info": "✅", "warning": "🔸", "critical": "❌"}[r.level]
        print(f"  {mark} {r.name}: {r.line}")

    bad = [r for r in readings if r.bad]
    if bad or args.always_notify:
        notify.notify_system_health(
            [(r.name, r.level, r.line) for r in readings], healthy=not bad,
        )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
