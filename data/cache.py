"""백테스트 재료(과거 시세·수급) parquet 캐시 (data 레이어).

이 캐시는 **백테스트 입력 데이터 전용**이다(external-apis §2 ①). 실전 운영 DB
(`journal.sqlite`)와 분리되며, 실전은 이 캐시를 읽지 않는다 → 실전 전환 시 `clear()`로
폴더째 비울 수 있다. 학습 결과(②)는 여기 아니라 journal.sqlite의 source='backtest'에 있다.

대용량·읽기전용·열 단위 분석에 맞춰 parquet 사용. `.gitignore`로 추적 제외.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent / "cache"


def path(name: str) -> Path:
    """캐시 파일 경로. name 예: 'ohlcv_005930', 'supply_005930', 'short_005930'."""
    return CACHE_DIR / f"{name}.parquet"


def save(name: str, df: pd.DataFrame) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = path(name)
    df.to_parquet(p, index=False)
    return p


def load(name: str) -> pd.DataFrame | None:
    """캐시가 있으면 DataFrame, 없으면 None."""
    p = path(name)
    return pd.read_parquet(p) if p.exists() else None


def exists(name: str) -> bool:
    return path(name).exists()


def clear() -> int:
    """백테스트 재료 캐시 전체 삭제(실전 전환용). 삭제한 파일 수 반환."""
    if not CACHE_DIR.exists():
        return 0
    files = list(CACHE_DIR.glob("*.parquet"))
    for f in files:
        f.unlink()
    return len(files)
