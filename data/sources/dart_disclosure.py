"""
description:        DART 기업행위 수집 (무상증자·감자·유상증자)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import io
import zipfile
from dataclasses import dataclass
from datetime import date
from xml.etree import ElementTree

import requests

from config.settings import get_settings

BASE = "https://opendart.fss.or.kr/api"
TIMEOUT_S = 20

# 기업행위 → DART 정형 API 엔드포인트
REPORT_APIS = {
    "bonus": "fricDecsn",       # 무상증자 결정
    "reduction": "crDecsn",     # 감자 결정
    "rights": "piicDecsn",      # 유상증자 결정
}

# 공시 제목에서 기업행위 종류를 알아내는 말머리
_TITLE_HINTS = (
    ("무상증자", "bonus"),
    ("감자", "reduction"),
    ("유상증자", "rights"),
)


class DartError(RuntimeError):
    """DART 수신·파싱 실패 격리용."""


@dataclass
class CorporateAction:
    """수집 결과 1건 — `CorporateActions` 한 행에 대응."""
    symbol_id: str
    ex_date: date               # 신주배정기준일·감자기준일
    action_type: str            # bonus / rights / reduction
    price_factor: float | None  # 전일 종가 → 당일 기준가 배수. 손절선 조정 비율
    detail: str                 # 배정비율·감자비율 원자료


def _key() -> str:
    """설정에서 DART API 키를 읽는다(없으면 에러)."""
    k = get_settings().dart_api_key
    if not k:
        raise DartError("dart_api_key 미설정 — 기업행위를 받을 수 없다")
    return k


def _get(endpoint: str, **params) -> dict:
    """DART JSON 호출. status '000'이 정상, '013'은 조회 결과 없음."""
    try:
        r = requests.get(f"{BASE}/{endpoint}.json",
                         params={"crtfc_key": _key(), **params}, timeout=TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        raise DartError(f"{endpoint} 호출 실패: {type(e).__name__}: {e}") from e
    status = body.get("status")
    if status == "013":                     # 조회된 데이터 없음
        return {"list": []}
    if status != "000":
        raise DartError(f"{endpoint} status={status} msg={body.get('message')}")
    return body


def fetch_disclosure_list(start: date, end: date, *, corp_code: str | None = None
                          ) -> list[dict]:
    """기간 내 공시목록에서 기업행위 결정 공시만 골라 action_type을 붙여 반환한다."""
    out: list[dict] = []
    page = 1
    while True:
        params = {
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_no": page,
            "page_count": 100,
            "pblntf_ty": "B",               # 발행공시
        }
        if corp_code:
            params["corp_code"] = corp_code
        body = _get("list", **params)
        for row in body.get("list", []):
            kind = _classify(row.get("report_nm", ""))
            if kind:
                out.append({**row, "action_type": kind})
        if page >= int(body.get("total_page") or 1):
            break
        page += 1
    return out


def _classify(title: str) -> str | None:
    """공시 제목으로 기업행위 종류를 판정한다(해당 없으면 None)."""
    for word, kind in _TITLE_HINTS:
        if word in title:
            return kind
    return None


def fetch_action(corp_code: str, action_type: str, *, symbol_id: str
                 ) -> CorporateAction | None:
    """한 회사의 정형 API로 기준일·배정비율을 꺼낸다(항목 없으면 None)."""
    endpoint = REPORT_APIS.get(action_type)
    if endpoint is None:
        return None
    body = _get(endpoint, corp_code=corp_code)
    rows = body.get("list") or []
    if not rows:
        return None
    row = rows[-1]                          # 가장 최근 결정
    ex = _parse_date(row.get("nstk_ascrt_bsis_dt") or row.get("cr_bsis_dt"))
    if ex is None:
        return None
    return CorporateAction(
        symbol_id=symbol_id,
        ex_date=ex,
        action_type=action_type,
        price_factor=_price_factor(action_type, row),
        detail=str(row),
    )


def _parse_date(v: str | None) -> date | None:
    """'YYYYMMDD' 형 문자열을 date로 변환한다(실패 시 None)."""
    if not v:
        return None
    digits = "".join(ch for ch in str(v) if ch.isdigit())
    if len(digits) != 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
    except ValueError:
        return None


def _price_factor(action_type: str, row: dict) -> float | None:
    """전일 종가 → 당일 기준가 배수를 계산한다(유상증자는 None)."""
    if action_type == "bonus":
        r = _num(row.get("nstk_ascrt_rt") or row.get("nstk_ascrt_bsis_stk_rt"))
        return 1.0 / (1.0 + r) if r and r > 0 else None
    if action_type == "reduction":
        r = _num(row.get("cr_rt"))          # 감자비율(%)
        return 1.0 / (1.0 - r / 100.0) if r and 0 < r < 100 else None
    return None


def _num(v) -> float | None:
    """문자열을 float로 변환한다(콤마·퍼센트 기호 제거, 실패 시 None)."""
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except ValueError:
        return None


def load_corp_code_map() -> dict[str, str]:
    """DART 회사코드 zip을 받아 종목코드 → 회사코드 매핑을 만든다."""
    try:
        r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": _key()},
                         timeout=TIMEOUT_S)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml = z.read(z.namelist()[0])
    except Exception as e:
        raise DartError(f"회사코드 zip 실패: {type(e).__name__}: {e}") from e
    out: dict[str, str] = {}
    for el in ElementTree.fromstring(xml).iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        corp = (el.findtext("corp_code") or "").strip()
        if len(stock) == 6 and corp:
            out[stock] = corp
    return out
