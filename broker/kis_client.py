"""KIS(한국투자증권) Open API 클라이언트 (Phase 1).

설계 불변식:
- **모드 분기 금지**(03-arch 3.3 / 11-2.9): `if mode == "real"` 같은 코드 분기 대신,
  모드별 차이(도메인·TR_ID·키·계좌)를 `_PROFILES` **데이터**로 두고 코드 경로는 하나다.
- **토큰 캐싱**(11-2.3): 24h 만료 + 발급 1분당 1회 제한(EGW00133) → 파일 캐시 재사용.
- **주문 송출 재시도 금지**(11-2.3): 조회만 5xx 백오프. 주문 POST는 중복위험 → 재시도 안 함.
- 진입 주문은 IOC지정가(ORD_DVSN=11, 11-2.8)를 기본값으로 둔다.

시크릿은 `config.settings`에서만 읽는다(여기에 키 값 없음).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from config.settings import Settings, get_settings, load_params
from core.timeutils import now_utc

# ── 모드별 차이 = 데이터(코드 분기 아님) ────────────────────────────────
# 시세성 TR(현재가·일봉·수급·공매도)은 실전/모의 공통(FH…)이라 프로필에 두지 않는다.
_PROFILES: dict[str, dict[str, Any]] = {
    "real": {
        "domain": "https://openapi.koreainvestment.com:9443",
        "key_attr": ("kis_app_key", "kis_app_secret", "kis_account_no"),
        "tr": {
            "balance": "TTTC8434R",
            "buy": "TTTC0802U",
            "sell": "TTTC0801U",
            "daily_orders": "TTTC0081R",
        },
    },
    "paper": {
        "domain": "https://openapivts.koreainvestment.com:29443",
        "key_attr": ("kis_paper_app_key", "kis_paper_app_secret", "kis_paper_account_no"),
        "tr": {
            "balance": "VTTC8434R",
            "buy": "VTTC0802U",
            "sell": "VTTC0801U",
            "daily_orders": "VTTC0081R",
        },
    },
}

# 시세 TR은 실전/모의 공통. 모의 도메인도 시세 4종을 지원함(2026-06-11 실측) → 모드 도메인 사용.
_QUOTE_TR = {
    "price": "FHKST01010100",
    "daily_chart": "FHKST03010100",
    "investor": "FHKST01010900",
    "short_sale": "FHPST04830000",
}

_RETRYABLE = {500, 502, 503, 504}
_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


class KISError(RuntimeError):
    """KIS 응답 오류(rt_cd != 0 또는 영구 HTTP 오류)."""


@dataclass
class _Token:
    access_token: str
    expires_at: datetime  # tz-aware UTC


class KISClient:
    """KIS REST 클라이언트. 모드(paper/real)는 생성 시 한 번 고정한다."""

    def __init__(self, mode: str | None = None, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        mode = mode or settings.trading_mode
        if mode not in _PROFILES:
            raise ValueError(f"알 수 없는 모드: {mode!r} (paper|real)")
        self.mode = mode
        self._p = _PROFILES[mode]
        ak, sk, acct = self._p["key_attr"]
        self._app_key: str = getattr(settings, ak)
        self._app_secret: str = getattr(settings, sk)
        if not self._app_key or not self._app_secret:
            raise KISError(f"{mode} 모드 KIS 키가 .env에 없음")
        # 계좌 "CANO-PRDT" 파싱
        raw = (getattr(settings, acct) or "").replace("-", "")
        self.cano = raw[:8]
        self.acnt_prdt = raw[8:10] or "01"

        rl = load_params("rate_limits")
        if mode == "paper":
            self._min_interval = float(rl["kis"]["paper"]["min_interval_sec"])
        else:
            per_sec = rl["kis"]["real"]["per_second"] * rl["kis"]["real"]["safe_ratio"]
            self._min_interval = 1.0 / per_sec
        self._max_retries = int(rl["anthropic"]["max_retries"])
        self._backoff_base = float(rl["anthropic"]["backoff_base_sec"])

        self._token: _Token | None = None
        self._last_call = 0.0
        _CACHE_DIR.mkdir(exist_ok=True)
        self._token_file = _CACHE_DIR / f"kis_token_{mode}.json"

    # ── 토큰 ────────────────────────────────────────────────────────
    def _load_cached_token(self) -> _Token | None:
        if self._token and self._token.expires_at > now_utc() + timedelta(minutes=10):
            return self._token
        if self._token_file.exists():
            data = json.loads(self._token_file.read_text())
            exp = datetime.fromisoformat(data["expires_at"])
            if exp > now_utc() + timedelta(minutes=10):
                self._token = _Token(data["access_token"], exp)
                return self._token
        return None

    def _issue_token(self) -> _Token:
        r = requests.post(
            f"{self._p['domain']}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            },
            timeout=10,
        )
        body = r.json()
        if "access_token" not in body:
            # EGW00133 = 발급 1분당 1회 제한
            ec, ed = body.get("error_code"), body.get("error_description")
            raise KISError(f"토큰 발급 실패: {ec} {ed}")
        # expires_in(초) 우선, 없으면 24h
        ttl = int(body.get("expires_in", 86400))
        tok = _Token(body["access_token"], now_utc() + timedelta(seconds=ttl))
        self._token_file.write_text(
            json.dumps({"access_token": tok.access_token, "expires_at": tok.expires_at.isoformat()})
        )
        self._token = tok
        return tok

    def _bearer(self) -> str:
        return (self._load_cached_token() or self._issue_token()).access_token

    # ── 공통 요청 ────────────────────────────────────────────────────
    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _headers(self, tr_id: str) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._bearer()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, domain: str, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        """조회 GET — 일시적 5xx는 지수 백오프 재시도(중복위험 없음)."""
        url = f"{domain}{path}"
        for attempt in range(self._max_retries):
            self._throttle()
            r = requests.get(url, headers=self._headers(tr_id), params=params, timeout=10)
            if r.status_code in _RETRYABLE and attempt < self._max_retries - 1:
                time.sleep(self._backoff_base * (2**attempt))
                continue
            return self._unwrap(r, tr_id)
        raise KISError(f"{tr_id} 재시도 소진")  # 도달 불가(루프가 반환)

    def _post_order(self, path: str, tr_id: str, body: dict[str, str]) -> dict[str, Any]:
        """주문 POST — 재시도 금지(중복 주문 방지, 11-2.3). 실패는 호출부가 체결조회로 확인."""
        self._throttle()
        r = requests.post(
            f"{self._p['domain']}{path}",
            headers=self._headers(tr_id),
            json=body,
            timeout=10,
        )
        return self._unwrap(r, tr_id)

    @staticmethod
    def _unwrap(r: requests.Response, tr_id: str) -> dict[str, Any]:
        if r.status_code in (401, 403, 404):
            raise KISError(f"{tr_id} 영구 오류 HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        if str(body.get("rt_cd", "0")) not in ("0", ""):
            raise KISError(f"{tr_id} rt_cd={body.get('rt_cd')} msg={body.get('msg1')}")
        return body

    # ── 조회 API ─────────────────────────────────────────────────────
    def get_balance(self) -> dict[str, Any]:
        """주식 잔고 조회."""
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        return self._get(self._p["domain"], path, self._p["tr"]["balance"], params)

    def get_price(self, code: str) -> dict[str, Any]:
        """현재가 조회."""
        path = "/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        return self._get(self._p["domain"], path, _QUOTE_TR["price"], params)

    def get_daily_chart(
        self, code: str, start: str, end: str, *, adjusted: bool = True
    ) -> list[dict[str, Any]]:
        """기간별 일봉(최대 ~100건/호출). start/end='YYYYMMDD'. 수정주가 기본."""
        path = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0" if adjusted else "1",
        }
        body = self._get(self._p["domain"], path, _QUOTE_TR["daily_chart"], params)
        return body.get("output2", [])

    def get_investor(self, code: str) -> list[dict[str, Any]]:
        """투자자별 수급(외국인·기관·개인). 최근 30거래일만(실측)."""
        path = "/uapi/domestic-stock/v1/quotations/inquire-investor"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        return self._get(self._p["domain"], path, _QUOTE_TR["investor"], params).get("output", [])

    def get_short_sale(self, code: str, start: str, end: str) -> list[dict[str, Any]]:
        """공매도 일별(~100건/호출, 과거 구간 가능 — 실측). start/end='YYYYMMDD'."""
        path = "/uapi/domestic-stock/v1/quotations/daily-short-sale"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
        }
        body = self._get(self._p["domain"], path, _QUOTE_TR["short_sale"], params)
        return body.get("output2", [])

    def get_daily_orders(self, date: str) -> list[dict[str, Any]]:
        """주식일별주문체결조회(모의 지원). 송출 실패 시 접수 확인용(11-2.3). date='YYYYMMDD'."""
        path = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt,
            "INQR_STRT_DT": date,
            "INQR_END_DT": date,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        return self._get(self._p["domain"], path, self._p["tr"]["daily_orders"], params).get(
            "output1", []
        )

    # ── 주문 API ─────────────────────────────────────────────────────
    def order_cash(
        self, code: str, qty: int, price: int, *, side: str, ord_dvsn: str = "11"
    ) -> dict[str, Any]:
        """현금 주문 송출. side='buy'|'sell'. ord_dvsn 기본 11(IOC지정가, 진입용 11-2.8).

        **재시도 금지** — 호출부는 KISError 시 get_daily_orders로 접수 여부를 확인할 것(11-2.3).
        시장가(01) 등 price 무의미한 유형은 ORD_UNPR='0'.
        """
        if side not in ("buy", "sell"):
            raise ValueError(f"side는 buy|sell: {side!r}")
        tr_id = self._p["tr"][side]
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt,
            "PDNO": code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        return self._post_order("/uapi/domestic-stock/v1/trading/order-cash", tr_id, body)
