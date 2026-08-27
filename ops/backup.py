"""백업 — pg_dump 스냅샷 (10-ops 10.6).

두 종류를 서로 다른 주기로 돌린다.

**① 거래 기록 백업 (매 사이클)** — 사이클이 끝나면 `Decisions`·`Orders`·`Positions`·
`Outcomes`만 덤프한다. 행 수가 적어 1초면 끝나고, **다시 만들 수 없는 유일한
데이터가 이것**이다.

**② 전체 백업 (하루 1회)** — 장 마감 후 DB 전체를 압축 스냅샷 하나로 뽑는다.
백업 중에도 매매·대시보드가 돈다.

**시장 데이터는 백업 대상이 아니다.** 일봉·수급·종목마스터는 언제든 다시 받을 수
있으므로 백업 용량만 키운다. 날아가면 배치를 다시 돌린다.

목표는 데이터 손실 1일·복구 4시간 이내다. 분기마다 백업에서 복원해 사이클 1회가
정상 동작하는지 확인한다 — 복원해 본 적 없는 백업은 백업이 아니다.

미구현 — 아래 함수는 전부 뼈대다.
"""
from __future__ import annotations

from pathlib import Path

# 매 사이클 덤프 대상 — 다시 만들 수 없는 표만. 시장 데이터는 제외(재수집 가능).
TRADE_TABLES = ("Decisions", "Orders", "Positions", "Outcomes")


def dump_trade_records(dest_dir: Path) -> Path:
    """① 거래 기록만 덤프. 반환: 만들어진 파일 경로."""
    raise NotImplementedError


def dump_full(dest_dir: Path) -> Path:
    """② DB 전체를 압축 스냅샷 1개로. 반환: 만들어진 파일 경로."""
    raise NotImplementedError


def prune_old(dest_dir: Path, *, keep_days: int) -> int:
    """보관 기간이 지난 스냅샷 삭제. 반환: 지운 개수."""
    raise NotImplementedError


def verify_restore(dump_path: Path) -> bool:
    """복구 리허설 — 백업을 임시 DB에 복원해 사이클 1회가 도는지 확인한다(분기 점검)."""
    raise NotImplementedError
