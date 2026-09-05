"""
description:        백업 — pg_dump 스냅샷 (사이클별 거래기록 + 일 1회 전체)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from config.settings import get_settings
from core.timeutils import now_utc

log = logging.getLogger(__name__)

# 매 사이클 덤프 대상 — 다시 만들 수 없는 표만. 시장 데이터는 제외(재수집 가능).
TRADE_TABLES = ("decisions", "orders", "positions", "outcomes")
DUMP_TIMEOUT_S = 600


class BackupError(RuntimeError):
    """백업 실패 격리용 — 호출 측이 알림으로 넘긴다(ops.notify)."""


def _stamp() -> str:
    """파일명용 UTC 타임스탬프 문자열을 만든다."""
    return now_utc().strftime("%Y%m%dT%H%M%SZ")


def _run(cmd: list[str]) -> None:
    """외부 명령 실행. 실패하면 stderr를 담아 BackupError로 올린다."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=DUMP_TIMEOUT_S)
    except FileNotFoundError as e:
        raise BackupError(f"{cmd[0]}를 찾을 수 없다 — PostgreSQL 클라이언트 설치 필요") from e
    except subprocess.TimeoutExpired as e:
        raise BackupError(f"{cmd[0]} 시간 초과({DUMP_TIMEOUT_S}s)") from e
    if p.returncode != 0:
        raise BackupError(f"{cmd[0]} 실패: {p.stderr.strip()[:400]}")


def dump_trade_records(dest_dir: Path, *, dsn: str | None = None) -> Path:
    """거래 기록(Decisions·Orders·Positions·Outcomes)만 덤프한다. 반환: 파일 경로."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"trades_{_stamp()}.dump"
    cmd = ["pg_dump", dsn or get_settings().db_dsn, "--format=custom", "--file", str(out)]
    for t in TRADE_TABLES:
        cmd += ["--table", f'public."{t}"']
    _run(cmd)
    return out


def dump_full(dest_dir: Path, *, dsn: str | None = None) -> Path:
    """DB 전체를 압축 스냅샷 1개로 덤프한다. 반환: 파일 경로."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"full_{_stamp()}.dump"
    _run(["pg_dump", dsn or get_settings().db_dsn,
          "--format=custom", "--compress=6", "--file", str(out)])
    return out


def prune_old(dest_dir: Path, *, keep_days: int) -> int:
    """보관 기간이 지난 스냅샷을 삭제한다(파일명 타임스탬프 기준). 반환: 지운 개수."""
    if not dest_dir.exists():
        return 0
    cutoff = now_utc().timestamp() - keep_days * 86400
    removed = 0
    for f in dest_dir.glob("*.dump"):
        stamp = f.stem.split("_")[-1]
        try:
            ts = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=now_utc().tzinfo
            ).timestamp()
        except ValueError:
            continue                       # 이름 규칙이 다른 파일은 건드리지 않는다
        if ts < cutoff:
            f.unlink()
            removed += 1
    return removed


def verify_restore(dump_path: Path, *, scratch_dsn: str) -> bool:
    """복구 리허설 — 백업을 임시 DB에 복원해 표가 살아나는지 확인한다."""
    try:
        _run(["pg_restore", "--dbname", scratch_dsn, "--clean", "--if-exists",
              str(dump_path)])
    except BackupError as e:
        log.error("복구 리허설 실패: %s", e)
        return False
    return True
