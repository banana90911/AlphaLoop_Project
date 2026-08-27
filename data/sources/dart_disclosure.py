"""DART 기업행위 수집 — 무상증자·감자·유상증자 (04-data 4.1 · 10-ops 10.18).

권리락 기준일과 배정비율을 받아 `CorporateActions`에 넣는다. 이 표가 있어야 두 가지가
된다: 과거 시계열의 수정주가 보정, 그리고 권리락 당일 보유 종목의 손절선 동반 조정.

**공시 본문을 읽지 않는다.** 무상증자·감자·유상증자 결정 공시는 기준일과 배정비율이
정해진 칸에 들어 있는 정형 API(fricDecsn·crDecsn·piicDecsn)로 제공된다. 배치가 공시
목록을 훑어 해당 종목의 정형 API만 호출하면 값이 그대로 나온다.

**배당락은 수집하지 않는다.** 거래소 수시공시라 DART 정형 API에 항목이 없고 제목만
목록에 뜬다. 낙폭이 1~3%로 손절폭(5~8%)보다 작아, 트레일링으로 손절선을 바짝 올린
상태에서만 문제가 된다. 감수하고 두되 KIS 예탁원 배당일정 조회는 확인 대상이다.

종목코드와 DART 회사코드는 체계가 달라 `Symbols.DartCorpCode`로 잇는다.

미구현 — 아래 함수는 전부 뼈대다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# DART 정형 API — 무상증자 / 감자 / 유상증자 결정
REPORT_APIS = {
    "bonus": "fricDecsn",       # 무상증자 결정
    "reduction": "crDecsn",     # 감자 결정
    "rights": "piicDecsn",      # 유상증자 결정
}


@dataclass
class CorporateAction:
    """수집 결과 1건 — `CorporateActions` 한 행에 대응."""
    symbol_id: str
    ex_date: date               # 신주배정기준일·감자기준일
    action_type: str            # bonus / rights / reduction / split / merger
    price_factor: float | None  # 전일 종가 → 당일 기준가 배수. 손절선 조정 비율
    detail: str                 # 배정비율·감자비율 원자료


def fetch_disclosure_list(start: date, end: date) -> list[dict]:
    """기간 내 공시목록을 훑어 기업행위 결정 공시만 골라낸다."""
    raise NotImplementedError


def fetch_action(corp_code: str, report_type: str) -> CorporateAction | None:
    """한 회사의 정형 API를 호출해 기준일·배정비율을 꺼낸다. 항목이 없으면 None."""
    raise NotImplementedError


def load_corp_code_map() -> dict[str, str]:
    """DART 회사코드 zip을 받아 종목코드 → 회사코드 매핑을 만든다(`Symbols.DartCorpCode`)."""
    raise NotImplementedError
