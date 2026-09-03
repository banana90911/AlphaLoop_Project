"""
description:        종목 유니버스 (KIS 종목마스터에서 보통주 목록 추출)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import io
import zipfile
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

_CACHE = Path(__file__).resolve().parent.parent / "cache" / "universe.parquet"
_BASE = "https://new.real.download.dws.co.kr/common/master"
# 시장별 (마스터 URL, 행 끝 고정폭 길이)
_MST = {
    "KOSPI": (f"{_BASE}/kospi_code.mst.zip", 227),
    "KOSDAQ": (f"{_BASE}/kosdaq_code.mst.zip", 221),
}


class UniverseError(RuntimeError):
    """마스터 파일 구조가 기대와 다름."""


def _download_lines(url: str) -> list[str]:
    """마스터 zip을 받아 cp949로 디코딩한 줄 목록을 반환한다."""
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    raw = z.read(z.namelist()[0]).decode("cp949")
    return raw.splitlines()


def _parse(lines: list[str], market: str, tail_len: int) -> pd.DataFrame:
    """고정폭 마스터 줄들을 code·name·market·group 표로 파싱한다."""
    rows = []
    for line in lines:
        if len(line) <= tail_len:
            continue
        head = line[: len(line) - tail_len]
        tail = line[len(line) - tail_len:]
        code = head[0:9].rstrip()
        name = head[21:].strip()
        group = tail[0:2]  # 증권그룹구분코드(ST=주권)
        if len(code) != 6:
            continue
        rows.append({"code": code, "name": name, "market": market, "group": group})
    if not rows:
        raise UniverseError(f"{market} 마스터 파싱 결과 0건 — 구조 변경 의심")
    return pd.DataFrame(rows)


def filter_common(df: pd.DataFrame) -> pd.DataFrame:
    """보통주만 남긴다(주권 + 우선주 배제 + 스팩 제외)."""
    return df[
        (df["group"] == "ST")
        & (df["code"].str.endswith("0"))     # 우선주(끝 5/7/9 등) 배제
        & (~df["name"].str.contains("스팩"))
    ]


def fetch_universe(*, common_only: bool = True) -> pd.DataFrame:
    """전종목 유니버스(code·name·market·group)를 조회한다."""
    df = pd.concat([_parse(_download_lines(u), m, t) for m, (u, t) in _MST.items()],
                   ignore_index=True)
    if common_only:
        df = filter_common(df)
    return df.drop_duplicates("code").sort_values("code").reset_index(drop=True)


@lru_cache(maxsize=1)
def load_market_map() -> dict[str, str]:
    """종목코드 → 시장(KOSPI/KOSDAQ) 룩업을 반환한다(캐시 우선)."""
    df = pd.read_parquet(_CACHE) if _CACHE.exists() else fetch_universe()
    return dict(zip(df["code"], df["market"]))
