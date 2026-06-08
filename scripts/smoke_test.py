"""
외부 API 연결 스모크 테스트.

목적: .env에 채운 키들이 실제로 응답하는지 하나씩 호출해 확인한다.
      "키가 유효한가 / 데이터가 들어오는가"를 보는 것이지, 전략 검증이 아니다.

주의(Phase 0):
  이 스크립트는 사용자의 맥북(한국 IP)에서 돈다. 여기서 KIS가 성공해도
  GitHub Actions(해외 IP)에서도 된다는 보장은 없다. KIS의 해외 IP 가용성은
  반드시 GitHub Actions 러너에서 따로 돌려 확인해야 한다(external-apis 10절).

실행: .venv/bin/python scripts/smoke_test.py
"""
from __future__ import annotations

import os
import sys
import traceback

import requests
from dotenv import load_dotenv

load_dotenv()

OK = "✅"
FAIL = "❌"
SKIP = "➖"


def line():
    print("-" * 60)


def need(*keys: str) -> bool:
    """필요한 환경변수가 모두 채워져 있는지."""
    return all(os.getenv(k) for k in keys)


# ─────────────────────────────────────────────────────────────
# KIS (한국투자증권)
# ─────────────────────────────────────────────────────────────
def kis_token(domain: str, app_key: str, app_secret: str) -> str:
    r = requests.post(
        f"{domain}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def kis_price(domain: str, token: str, app_key: str, app_secret: str) -> int:
    """삼성전자(005930) 현재가."""
    r = requests.get(
        f"{domain}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers={
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "FHKST01010100",
            "custtype": "P",
        },
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"},
        timeout=10,
    )
    r.raise_for_status()
    return int(r.json()["output"]["stck_prpr"])


def kis_balance(domain: str, token: str, app_key: str, app_secret: str,
                account_no: str, tr_id: str) -> str:
    """계좌 잔고 조회 — 계좌번호 유효성 확인용."""
    cano, _, prdt = account_no.partition("-")
    r = requests.get(
        f"{domain}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers={
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        },
        params={
            "CANO": cano,
            "ACNT_PRDT_CD": prdt or "01",
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("rt_cd") != "0":
        raise RuntimeError(f"rt_cd={body.get('rt_cd')} msg={body.get('msg1')}")
    cash = body["output2"][0]["dnca_tot_amt"] if body.get("output2") else "0"
    return cash


def test_kis(name: str, domain: str, kp: str, sp: str, acc: str, bal_tr: str):
    print(f"[KIS {name}] {domain}")
    if not need(kp, sp, acc):
        print(f"  {SKIP} 키 미설정 ({kp}/{sp}/{acc})")
        return
    app_key, app_secret, account_no = os.getenv(kp), os.getenv(sp), os.getenv(acc)
    try:
        token = kis_token(domain, app_key, app_secret)
        print(f"  {OK} 토큰 발급 (App Key/Secret 유효)")
    except Exception as e:
        print(f"  {FAIL} 토큰 발급 실패: {e}")
        return
    try:
        price = kis_price(domain, token, app_key, app_secret)
        print(f"  {OK} 시세 조회: 삼성전자 현재가 {price:,}원")
    except Exception as e:
        print(f"  {FAIL} 시세 조회 실패: {e}")
    try:
        cash = kis_balance(domain, token, app_key, app_secret, account_no, bal_tr)
        print(f"  {OK} 잔고 조회: 예수금 {int(cash):,}원 (계좌번호 {account_no} 유효)")
    except Exception as e:
        print(f"  {FAIL} 잔고 조회 실패 (계좌번호/연동 확인 필요): {e}")


# ─────────────────────────────────────────────────────────────
# 나머지 서비스
# ─────────────────────────────────────────────────────────────
def test_anthropic():
    print("[Anthropic Claude]")
    if not need("ANTHROPIC_API_KEY"):
        print(f"  {SKIP} ANTHROPIC_API_KEY 미설정")
        return
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model="claude-haiku-4-5",  # 스모크 테스트는 가장 싼 모델로
            max_tokens=10,
            messages=[{"role": "user", "content": "Reply with just: pong"}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        print(f"  {OK} 응답 수신: {text!r} (크레딧/키 유효, 모델 haiku)")
    except Exception as e:
        print(f"  {FAIL} 호출 실패 (크레딧 충전 여부 확인): {e}")


def test_naver():
    print("[네이버 검색]")
    if not need("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
        print(f"  {SKIP} NAVER 키 미설정")
        return
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={
                "X-Naver-Client-Id": os.getenv("NAVER_CLIENT_ID"),
                "X-Naver-Client-Secret": os.getenv("NAVER_CLIENT_SECRET"),
            },
            params={"query": "삼성전자", "display": 1},
            timeout=10,
        )
        r.raise_for_status()
        total = r.json().get("total", 0)
        print(f"  {OK} 뉴스 검색: '삼성전자' 결과 {total:,}건")
    except Exception as e:
        print(f"  {FAIL} 검색 실패: {e}")


def test_dart():
    print("[DART 전자공시]")
    if not need("DART_API_KEY"):
        print(f"  {SKIP} DART_API_KEY 미설정")
        return
    try:
        r = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={"crtfc_key": os.getenv("DART_API_KEY"), "page_count": 1},
            timeout=10,
        )
        r.raise_for_status()
        body = r.json()
        status = body.get("status")
        if status == "000":
            print(f"  {OK} 공시 목록 조회 OK (status=000)")
        elif status == "013":
            print(f"  {OK} 키 유효 (status=013: 조회 데이터 없음 — 정상)")
        else:
            print(f"  {FAIL} status={status} msg={body.get('message')}")
    except Exception as e:
        print(f"  {FAIL} 조회 실패: {e}")


def test_fred():
    print("[FRED 매크로]")
    if not need("FRED_API_KEY"):
        print(f"  {SKIP} FRED_API_KEY 미설정")
        return
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "DGS10",  # 美 10년 국채금리
                "api_key": os.getenv("FRED_API_KEY"),
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            },
            timeout=10,
        )
        r.raise_for_status()
        obs = r.json()["observations"][0]
        print(f"  {OK} DGS10 최신값: {obs['value']}% ({obs['date']})")
    except Exception as e:
        print(f"  {FAIL} 조회 실패: {e}")


def test_yfinance():
    print("[yfinance 글로벌 시세] (키 불필요)")
    try:
        import yfinance as yf

        hist = yf.Ticker("^GSPC").history(period="5d")
        if hist.empty:
            print(f"  {FAIL} 데이터 비어 있음 (yfinance 차단/장애 가능)")
            return
        last = hist["Close"].iloc[-1]
        print(f"  {OK} S&P500 최근 종가: {last:,.2f}")
    except Exception as e:
        print(f"  {FAIL} 조회 실패: {e}")


def test_healthcheck():
    print("[healthchecks.io 데드맨스위치]")
    url = os.getenv("HEALTHCHECK_URL")
    if not url:
        print(f"  {SKIP} HEALTHCHECK_URL 미설정")
        return
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        print(f"  {OK} ping 전송 OK (대시보드에 '1회 수신'으로 뜸)")
    except Exception as e:
        print(f"  {FAIL} ping 실패: {e}")


def test_discord():
    print("[Discord 웹훅] (실제로 채널에 메시지가 갑니다)")
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        print(f"  {SKIP} DISCORD_WEBHOOK_URL 미설정")
        return
    try:
        r = requests.post(url, json={"content": "🔔 StockAI 스모크 테스트 — 웹훅 정상"}, timeout=10)
        r.raise_for_status()
        print(f"  {OK} 메시지 전송 OK (디스코드 채널 확인)")
    except Exception as e:
        print(f"  {FAIL} 전송 실패: {e}")


def main():
    print("=" * 60)
    print(" 외부 API 연결 스모크 테스트")
    print("=" * 60)

    test_kis("실전", "https://openapi.koreainvestment.com:9443",
             "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO", "TTTC8434R")
    line()
    test_kis("모의", "https://openapivts.koreainvestment.com:29443",
             "KIS_PAPER_APP_KEY", "KIS_PAPER_APP_SECRET", "KIS_PAPER_ACCOUNT_NO", "VTTC8434R")
    line()
    test_anthropic()
    line()
    test_naver()
    line()
    test_dart()
    line()
    test_fred()
    line()
    test_yfinance()
    line()
    test_healthcheck()
    line()
    test_discord()
    print("=" * 60)
    print("주의: 위 KIS 성공은 '한국 IP'에서의 결과입니다.")
    print("      GitHub Actions(해외 IP) 가용성은 별도 확인이 필요합니다(Phase 0).")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
