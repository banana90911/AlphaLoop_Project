"""
description:        KIS(한국투자증권) Open API 클라이언트
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from config.settings import Settings, get_settings, load_params
from core.timeutils import now_utc

# ── 모드별 차이 = 데이터(코드 분기 아님) ────────────────────────────────
# TR(Transaction) ID: KIS API가 요청의 "기능 종류"를 구분하는 코드. URL 경로는 같아도
# 헤더의 tr_id로 잔고조회·매수·매도 등을 구분한다(KIS 공식 문서 용어, 여기서 새로 만든 게 아님).
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


def _num(v: Any) -> float:
    """KIS 응답의 숫자 — 문자열로 오고 빈 값·None이 섞인다. 못 읽으면 0.0."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Holding:
    """정규화한 보유 1건. KIS 응답 필드명을 바깥으로 새어나가지 않게 하는 경계."""
    code: str
    qty: int
    avg_price: float
    last_price: float
    eval_amount: float


@dataclass
class Balance:
    """정규화한 계좌 스냅샷(base_asset=전일 총자산 = 서킷브레이커 기준선, 05-risk 5.2)."""
    cash: float
    total_asset: float
    base_asset: float
    holdings: list[Holding]


class KISClient:
    """KIS REST 클라이언트"""

    def __init__(self, mode: str | None = None, settings: Settings | None = None) -> None:
        """모드(real/paper)별 프로필·인증키·요청 한도를 로드한다."""
        settings = settings or get_settings()
        mode = mode or settings.trading_mode
        if mode not in _PROFILES:
            raise ValueError(f"알 수 없는 모드: {mode!r} (paper|real)")
        self.mode = mode
        self._profile = _PROFILES[mode]
        ak, sk, acct = self._profile["key_attr"]
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
        self._max_retries = int(rl["retry"]["max_retries"])
        self._backoff_base = float(rl["retry"]["backoff_base_sec"])

        self._token: _Token | None = None
        self._last_call = 0.0
        _CACHE_DIR.mkdir(exist_ok=True)
        self._token_file = _CACHE_DIR / f"kis_token_{mode}.json"

    # ── 토큰 ────────────────────────────────────────────────────────
    def _load_cached_token(self) -> _Token | None:
        """캐시된 토큰이 유효하면(만료 10분 전까지) 반환, 아니면 None."""
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
        """새 액세스 토큰을 발급받아 캐시 파일에 저장한다."""
        response = requests.post(
            f"{self._profile['domain']}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            },
            timeout=10,
        )
        body = response.json()
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
        """유효한 액세스 토큰 문자열을 반환한다(캐시 우선, 없으면 발급)."""
        return (self._load_cached_token() or self._issue_token()).access_token

    # ── 공통 요청 ────────────────────────────────────────────────────
    def _throttle(self) -> None:
        """직전 호출과의 최소 간격을 지킨다(레이트 리밋 준수)."""
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _headers(self, tr_id: str) -> dict[str, str]:
        """인증·TR ID가 포함된 공통 요청 헤더를 만든다."""
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._bearer()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, domain: str, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        """조회 GET — 일시적 5xx는 지수 백오프로 재시도한다."""
        url = f"{domain}{path}"
        for attempt in range(self._max_retries):
            self._throttle()
            response = requests.get(
                url, headers=self._headers(tr_id), params=params, timeout=10
            )
            if response.status_code in _RETRYABLE and attempt < self._max_retries - 1:
                time.sleep(self._backoff_base * (2**attempt))
                continue
            return self._unwrap(response, tr_id)
        raise KISError(f"{tr_id} 재시도 소진")

    def _post_order(self, path: str, tr_id: str, body: dict[str, str]) -> dict[str, Any]:
        """주문 POST — 재시도하지 않는다(중복 주문 방지)."""
        self._throttle()
        response = requests.post(
            f"{self._profile['domain']}{path}",
            headers=self._headers(tr_id),
            json=body,
            timeout=10,
        )
        return self._unwrap(response, tr_id)

    @staticmethod
    def _unwrap(response: requests.Response, tr_id: str) -> dict[str, Any]:
        """HTTP/응답코드 오류를 검사하고 본문(JSON)만 꺼내 반환한다."""
        if response.status_code in (401, 403, 404):
            raise KISError(f"{tr_id} 영구 오류 HTTP {response.status_code}: {response.text[:200]}")
        body = response.json()
        if str(body.get("rt_cd", "0")) not in ("0", ""):
            raise KISError(f"{tr_id} rt_cd={body.get('rt_cd')} msg={body.get('msg1')}")
        return body

    # ── 조회 API ─────────────────────────────────────────────────────
    def get_balance(self) -> dict[str, Any]:
        """주식 잔고 조회"""
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

        return self._get(self._profile["domain"], path, self._profile["tr"]["balance"], params)

    def fetch_balance(self) -> Balance:
        """잔고를 정규화해서 반환"""
        body = self.get_balance()
        summary = (body.get("output2") or [{}])[0]

        holdings = [
            Holding(
                code=row.get("pdno", ""),
                qty=int(_num(row.get("hldg_qty"))),
                avg_price=_num(row.get("pchs_avg_pric")),
                last_price=_num(row.get("prpr")),
                eval_amount=_num(row.get("evlu_amt")),
            )
            for row in (body.get("output1") or [])
            if int(_num(row.get("hldg_qty"))) > 0
        ]
        cash = _num(summary.get("dnca_tot_amt"))

        return Balance(
            cash=cash,
            total_asset=_num(summary.get("tot_evlu_amt")) or cash,
            # 전일 총자산이 비면(계좌 개설 첫날 등) 오늘 총자산을 기준선으로 삼는다
            base_asset=(_num(summary.get("bfdy_tot_asst_evlu_amt"))
                             or _num(summary.get("tot_evlu_amt")) or cash),
            holdings=holdings,
        )

    def get_holidays(self, base_date: date) -> list[dict[str, Any]]:
        """국내 휴장일 조회"""
        path = "/uapi/domestic-stock/v1/quotations/chk-holiday"
        params = {
            "BASS_DT": base_date.strftime("%Y%m%d"),
            "CTX_AREA_NK": "",
            "CTX_AREA_FK": "",
        }
        body = self._get(self._profile["domain"], path, "CTCA0903R", params)
        return body.get("output", [])

    def get_price(self, code: str) -> dict[str, Any]:
        """현재가 조회"""
        path = "/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        return self._get(self._profile["domain"], path, _QUOTE_TR["price"], params)

    def get_daily_chart(
        self, code: str, start: str, end: str, *, adjusted: bool = True
    ) -> list[dict[str, Any]]:
        """기간별 일봉(최대 ~100건/호출) 조회. start/end='YYYYMMDD'."""
        path = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0" if adjusted else "1",
        }
        body = self._get(self._profile["domain"], path, _QUOTE_TR["daily_chart"], params)
        return body.get("output2", [])

    def get_investor(self, code: str) -> list[dict[str, Any]]:
        """투자자별 수급(외국인·기관·개인) 조회, 최근 30거래일."""
        path = "/uapi/domestic-stock/v1/quotations/inquire-investor"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        return self._get(
            self._profile["domain"], path, _QUOTE_TR["investor"], params
        ).get("output", [])

    def get_short_sale(self, code: str, start: str, end: str) -> list[dict[str, Any]]:
        """공매도 일별(~100건/호출) 조회. start/end='YYYYMMDD'."""
        path = "/uapi/domestic-stock/v1/quotations/daily-short-sale"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
        }
        body = self._get(self._profile["domain"], path, _QUOTE_TR["short_sale"], params)
        return body.get("output2", [])

    def get_daily_orders(self, date: str) -> list[dict[str, Any]]:
        """주식일별주문체결조회(date='YYYYMMDD') — 송출 실패 시 접수 확인용."""
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
        return self._get(
            self._profile["domain"], path, self._profile["tr"]["daily_orders"], params
        ).get("output1", [])

    # ── 주문 API ─────────────────────────────────────────────────────
    def order_cash(
        self, code: str, qty: int, price: int, *, side: str, ord_dvsn: str = "11",
        cndt_pric: int | None = None,
    ) -> dict[str, Any]:
        """현금 주문 1회 송출(재시도 없음). side='buy'|'sell'."""
        if side not in ("buy", "sell"):
            raise ValueError(f"side는 buy|sell: {side!r}")
        tr_id = self._profile["tr"][side]
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt,
            "PDNO": code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        if cndt_pric is not None:
            body["CNDT_PRIC"] = str(cndt_pric)
        return self._post_order("/uapi/domestic-stock/v1/trading/order-cash", tr_id, body)

    def place_stop(
        self, *, code: str, qty: int, trigger_price: int, limit_price: int,
        client_order_id: str,
    ) -> "Fill":
        """손절 스톱지정가(22) 등록 — 진입 체결 직후 호출돼 장간 갭 손절을 대비한다."""
        from exec.orders import Fill
        try:
            resp = self.order_cash(
                code, qty, limit_price, side="sell", ord_dvsn="22",
                cndt_pric=trigger_price,
            )
            out = resp.get("output") or resp
            return Fill(0, None, "submitted", out.get("ODNO") or out.get("odno"))
        except KISError:
            return Fill(0, None, "rejected")

    def place_exit(
        self, *, code: str, qty: int, ord_dvsn: str, client_order_id: str
    ) -> "Fill":
        """청산 송출 + 일별체결조회로 체결 확정(exec.orders.Broker 프로토콜)."""
        from exec.orders import Fill
        odno: str | None = None
        try:
            resp = self.order_cash(code, qty, 0, side="sell", ord_dvsn=ord_dvsn)
            out = resp.get("output") or resp
            odno = out.get("ODNO") or out.get("odno")
        except KISError:
            pass
        try:
            rows = self.get_daily_orders(_today_kst())
        except KISError:
            return Fill(0, None, "submitted", odno)
        match = next((r for r in rows if odno and r.get("odno") == odno), None)
        if match is None:
            cands = [r for r in rows if r.get("pdno") == code]
            match = cands[-1] if cands else None
        if match is None:
            return Fill(0, None, "rejected" if odno is None else "submitted", odno)
        filled = int(match.get("tot_ccld_qty") or 0)
        avg = float(match.get("avg_prvs") or 0) or None
        status = "filled" if filled >= qty else ("partial" if filled > 0 else "submitted")
        return Fill(filled, avg, status, odno)

    def place_entry(
        self, *, code: str, qty: int, price: int, ord_dvsn: str, client_order_id: str
    ) -> "Fill":
        """신규 진입 송출 + 일별체결조회로 체결 확정(exec.orders.Broker 프로토콜)."""
        from exec.orders import Fill
        odno: str | None = None
        try:
            resp = self.order_cash(code, qty, price, side="buy", ord_dvsn=ord_dvsn)
            out = resp.get("output") or resp
            odno = out.get("ODNO") or out.get("odno")
        except KISError:
            pass
        try:
            rows = self.get_daily_orders(_today_kst())
        except KISError:
            return Fill(0, None, "submitted", odno)
        match = next((r for r in rows if odno and r.get("odno") == odno), None)
        if match is None:
            cands = [r for r in rows if r.get("pdno") == code]
            match = cands[-1] if cands else None
        if match is None:
            return Fill(0, None, "rejected" if odno is None else "submitted", odno)
        filled = int(match.get("tot_ccld_qty") or 0)
        avg = float(match.get("avg_prvs") or 0) or None
        status = "filled" if filled >= qty else ("partial" if filled > 0 else "submitted")
        return Fill(filled, avg, status, odno)


def _today_kst() -> str:
    """오늘 거래일(KST) 'YYYYMMDD' 문자열을 반환한다."""
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")
