"""종목 유니버스 — KIS 종목 마스터(.mst)에서 백테스트 대상 종목 목록 (data 레이어).

KIS는 전종목 목록을 REST가 아니라 마스터 파일로 제공한다. 코스피·코스닥 .mst.zip을
받아 단축코드·종목명·시장·증권그룹구분코드를 파싱한다(cp949 고정폭).

기본은 **보통주만**(04-data §후보제외: 우선주·스팩·ETF·ETN·리츠 제외) — 증권그룹구분
코드 'ST' + 단축코드 끝자리 '0'(우선주 배제) + 이름에 '스팩' 없음.

한계: 마스터는 **현재 상장 종목만** 담는다(상폐 종목 미포함) → 완전한 생존편향 차단은
상폐 종목 목록 보강이 별도로 필요(추후). 시세 자체는 네이버 sise_day로 상폐도 수집 가능.
"""
from __future__ import annotations

import io
import zipfile
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

_CACHE = Path(__file__).resolve().parent.parent / "cache" / "universe.parquet"
_BASE = "https://new.real.download.dws.co.kr/common/master"
# 시장별 (마스터 URL, 행 끝 고정폭 길이). 끝 길이는 시장마다 다름(splitlines 후 실측값).
_MST = {
    "KOSPI": (f"{_BASE}/kospi_code.mst.zip", 227),
    "KOSDAQ": (f"{_BASE}/kosdaq_code.mst.zip", 221),
}


class UniverseError(RuntimeError):
    """마스터 파일 구조가 기대와 다름."""


def _download_lines(url: str) -> list[str]:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    raw = z.read(z.namelist()[0]).decode("cp949")
    return raw.splitlines()


def _parse(lines: list[str], market: str, tail_len: int) -> pd.DataFrame:
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
    """보통주만(04-data §후보제외): 주권(ST) + 단축코드 끝 '0'(우선주 배제) + 스팩 제외."""
    return df[
        (df["group"] == "ST")
        & (df["code"].str.endswith("0"))     # 우선주(끝 5/7/9 등) 배제
        & (~df["name"].str.contains("스팩"))
    ]


def fetch_universe(*, common_only: bool = True) -> pd.DataFrame:
    """전종목 유니버스. 컬럼 code·name·market·group. common_only면 보통주만."""
    df = pd.concat([_parse(_download_lines(u), m, t) for m, (u, t) in _MST.items()],
                   ignore_index=True)
    if common_only:
        df = filter_common(df)
    return df.drop_duplicates("code").sort_values("code").reset_index(drop=True)


@lru_cache(maxsize=1)
def load_market_map() -> dict[str, str]:
    """종목코드 → 시장(KOSPI/KOSDAQ) 룩업. 거래세율·비용 산식(positions.market)용.

    캐시 parquet(`data/cache/universe.parquet`)을 우선 읽고, 없으면 마스터를 내려받는다.
    마스터 다운로드는 *공개 파일 읽기*(주문 송출 아님)라 비용·위험이 없다. 업종(섹터)은
    마스터의 지수업종분류가 지수 미편입 종목에 기본값이 몰려 부적합 → 별도 소스 필요(미결).
    """
    df = pd.read_parquet(_CACHE) if _CACHE.exists() else fetch_universe()
    return dict(zip(df["code"], df["market"]))
