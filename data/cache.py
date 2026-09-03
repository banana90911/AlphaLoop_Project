"""
description:        백테스트 재료(과거 시세·수급) parquet 캐시
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent / "cache"


def path(name: str) -> Path:
    """캐시 파일 경로. name 예: 'ohlcv_005930', 'supply_005930', 'short_005930'."""
    return CACHE_DIR / f"{name}.parquet"


def save(name: str, df: pd.DataFrame) -> Path:
    """DataFrame을 parquet으로 저장하고 경로를 반환한다."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = path(name)
    df.to_parquet(p, index=False)
    return p


def load(name: str) -> pd.DataFrame | None:
    """캐시가 있으면 DataFrame, 없으면 None."""
    p = path(name)
    return pd.read_parquet(p) if p.exists() else None


def exists(name: str) -> bool:
    """해당 이름의 캐시 파일 존재 여부."""
    return path(name).exists()


def clear() -> int:
    """백테스트 재료 캐시 전체 삭제(실전 전환용). 삭제한 파일 수 반환."""
    if not CACHE_DIR.exists():
        return 0
    files = list(CACHE_DIR.glob("*.parquet"))
    for f in files:
        f.unlink()
    return len(files)
