"""백테스트 데이터 수집 오케스트레이션 (data 레이어, 배치 실행기).

유니버스 종목을 돌며 가격·공매도(KIS)·수급(네이버)을 받아 parquet 캐시에 적재한다.
- **이어받기**: 이미 캐시에 있는 종목·종류는 건너뛴다(중단 후 재개 안전).
- **격리**: 한 종목 실패가 전체를 멈추지 않는다(실패 목록만 모아 보고).
- KIS 시세 정본은 실전 도메인 → 기본 `mode='real'`(읽기 전용 조회라 위험 없음).

실행:  python -m data.collect --start 20160101 --end 20260612 [--limit N] [--force]
전체 2500여 종목은 KIS rate limit(0.6s/호출)로 수 시간 — 먼저 --limit로 소규모 검증 권장.
"""
from __future__ import annotations

import argparse

import requests

from broker.kis_client import KISClient
from data import cache
from data.sources import kis_history, naver_finance
from data.sources.universe import fetch_universe


def collect_one(
    client: KISClient,
    naver: requests.Session,
    code: str,
    start: str,
    end: str,
    *,
    force: bool = False,
) -> dict[str, object]:
    """한 종목의 ohlcv·short·supply를 수집·캐시. 종류별 행수(또는 'skip') 반환."""
    plan = {
        "ohlcv": lambda: kis_history.fetch_ohlcv_range(client, code, start, end),
        "short": lambda: kis_history.fetch_short_sale_range(client, code, start, end),
        "supply": lambda: naver_finance.fetch_supply(code, start, end, session=naver),
    }
    result: dict[str, object] = {}
    for kind, fetch in plan.items():
        name = f"{kind}_{code}"
        if not force and cache.exists(name):
            result[kind] = "skip"
            continue
        df = fetch()
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
) -> dict[str, list]:
    """유니버스 전체(또는 codes/limit) 수집. 실패 목록을 모아 반환."""
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
            r = collect_one(client, naver, code, start, end, force=force)
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
    ap = argparse.ArgumentParser(description="백테스트 데이터 수집")
    ap.add_argument("--start", required=True, help="YYYYMMDD")
    ap.add_argument("--end", required=True, help="YYYYMMDD")
    ap.add_argument("--limit", type=int, default=None, help="앞 N종목만(소규모 검증)")
    ap.add_argument("--force", action="store_true", help="캐시 무시 재수집")
    args = ap.parse_args()
    collect_universe(args.start, args.end, limit=args.limit, force=args.force)


if __name__ == "__main__":
    main()
