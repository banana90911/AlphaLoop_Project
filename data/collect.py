"""
description:        백테스트 데이터 수집 오케스트레이션 (배치 실행기)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import argparse

import requests

from broker.kis_client import KISClient
from data import cache
from data.sources import kis_history, naver_finance
from data.sources.universe import fetch_universe

_ALL_KINDS = ("ohlcv", "short", "supply")


def collect_one(
    client: KISClient,
    naver: requests.Session,
    code: str,
    start: str,
    end: str,
    *,
    force: bool = False,
    kinds: tuple[str, ...] = _ALL_KINDS,
) -> dict[str, object]:
    """한 종목의 가격·수급을 수집·캐시한다. 종류별 행수(또는 'skip')를 반환."""
    plan = {
        "ohlcv": lambda: kis_history.fetch_ohlcv_range(client, code, start, end),
        "short": lambda: kis_history.fetch_short_sale_range(client, code, start, end),
        "supply": lambda: naver_finance.fetch_supply(code, start, end, session=naver),
    }
    result: dict[str, object] = {}
    for kind in kinds:
        name = f"{kind}_{code}"
        if not force and cache.exists(name):
            result[kind] = "skip"
            continue
        df = plan[kind]()
        cache.save(name, df)
        result[kind] = len(df)
    return result


def collect_universe(
    start: str,
    end: str,
    *,
    limit: int | None = None,
    force: bool = False,
    codes: list[str] | None = None,
    kinds: tuple[str, ...] = _ALL_KINDS,
) -> dict[str, list]:
    """유니버스 전체(또는 codes/limit)를 수집한다. 실패 목록을 모아 반환."""
    if codes is None:
        uni = fetch_universe()
        cache.save("universe", uni)
        codes = uni["code"].tolist()
    if limit:
        codes = codes[:limit]

    client = KISClient(mode="real")
    naver = naver_finance._new_session()

    done, failed = [], []
    for i, code in enumerate(codes, 1):
        try:
            r = collect_one(client, naver, code, start, end, force=force, kinds=kinds)
            done.append(code)
            print(f"[{i}/{len(codes)}] {code} {r}")
        except Exception as e:  # 한 종목 실패가 배치를 멈추지 않음
            failed.append((code, f"{type(e).__name__}: {e}"))
            print(f"[{i}/{len(codes)}] {code} 실패: {type(e).__name__}: {e}")

    print(f"\n완료 {len(done)} / 실패 {len(failed)}")
    for code, err in failed:
        print(f"  실패 {code}: {err}")
    return {"done": done, "failed": failed}


def main() -> None:
    """CLI 진입점 — 유니버스 종목의 가격·수급을 parquet 캐시로 수집한다."""
    ap = argparse.ArgumentParser(description="백테스트 데이터 수집")
    ap.add_argument("--start", required=True, help="YYYYMMDD")
    ap.add_argument("--end", required=True, help="YYYYMMDD")
    ap.add_argument("--limit", type=int, default=None, help="앞 N종목만(소규모 검증)")
    ap.add_argument("--force", action="store_true", help="캐시 무시 재수집")
    ap.add_argument("--no-short", action="store_true",
                    help="공매도 수집 제외(현 엔진 미사용, KIS 호출 절감)")
    args = ap.parse_args()
    kinds = tuple(k for k in _ALL_KINDS if not (args.no_short and k == "short"))
    collect_universe(args.start, args.end, limit=args.limit, force=args.force, kinds=kinds)


if __name__ == "__main__":
    main()
