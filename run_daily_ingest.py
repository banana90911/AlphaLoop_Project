"""장 시작 전 일일 배치 진입점 (사이클 밖, 하루 1회 — 03-arch 3.1).

전 종목 데이터를 미리 준비하는 배치다. 종목 2,500여 개를 사이클마다 조회하는 것은
KIS 호출 한도상 불가능하고, 일봉·수급·종목 상태는 하루 한 번만 바뀌기 때문에 따로 돈다.

실행 시각은 아침이 낫다 — 저녁과 받는 내용은 같지만 밤사이 데이터 정정이 반영된다.

**단계별로 `IngestRuns`에 한 줄씩 남기는 것이 이 배치의 핵심 계약이다.** 매 사이클의
데이터 신선도 검사가 그 표를 읽어, 오늘 배치가 `ok`가 아니거나 종료 시각이 너무
오래됐으면 사이클을 진행하지 않는다(10-ops 10.3). 조용히 실패하면 그날 하루 판단
전체가 낡은 데이터 위에서 이뤄진다.

`partial`이면 성공 건수를 이어받기 기준으로 삼아 실패한 종목만 재시도한다 — 전체를
다시 받지 않는다.

미구현 — 아래 함수는 전부 뼈대다.
"""
from __future__ import annotations

import argparse
from datetime import date


def ingest_symbols(conn, *, trade_date: date) -> None:
    """① 종목 명부·상태 → `Symbols`·`SymbolStates`.

    KIS 종목마스터 파일 2개(코스피·코스닥)를 받아 전 종목의 시장·증권종류·상장일과
    관리종목·거래정지·투자경고·단기과열 지정 여부를 적재한다. 명부는 통째로 덮어쓰고,
    상태는 딱지가 붙은 종목만 날짜별로 쌓는다(백테스트 룩어헤드 차단 — 07-model 7.1).

    수집 자체는 `data/sources/universe.py`가 이미 한다 — 여기서는 DB 적재만 붙인다.
    """
    raise NotImplementedError


def ingest_bars_and_flows(conn, *, trade_date: date) -> None:
    """② 일봉·수급 → `DailyBars`·`DailyFlows`. 종목당 1호출로 어제 확정분 한 줄씩 추가."""
    raise NotImplementedError


def ingest_corporate_actions(conn, *, trade_date: date) -> None:
    """③ 기업행위 → `CorporateActions`.

    DART 공시목록을 훑어 권리락 기준일·배정비율을 적재한다(`data/sources/dart_disclosure`).
    전 상장사 기준 월 10건 안팎이라 호출 비용이 작다.
    """
    raise NotImplementedError


def ingest_indices(conn, *, trade_date: date) -> None:
    """④ 지수 → `MarketIndices`. 코스피·코스닥 종가와 200일선(호출 2회)."""
    raise NotImplementedError


def compute_daily_scores(conn, *, trade_date: date) -> None:
    """⑤ 전 종목 점수 → `DailyScores`.

    제외 필터를 적용하고, **통과 종목 집합 안에서** 백분위로 종합점수·순위를 계산한다.
    이 점수가 그날 정기 사이클 1단계의 입력이다. 원시값과 백분위를 함께 저장해야
    나중에 가중치를 바꿔 재계산할 수 있다(백분위는 그날 통과 집합에 의존).

    계산은 `data/features.py`·`data/screener.py`가 이미 한다 — 여기서는 전 종목으로
    돌려 DB에 적재하는 것만 붙인다.
    """
    raise NotImplementedError


def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaLoop 일일 배치")
    parser.add_argument("--date", help="대상 거래일(YYYY-MM-DD). 생략 시 오늘(KST)")
    parser.add_argument("--resume", action="store_true",
                        help="직전 partial 실행을 이어받아 실패한 종목만 재시도")
    parser.parse_args()
    raise NotImplementedError


if __name__ == "__main__":
    main()
