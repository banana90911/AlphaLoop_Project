# 외부 API·데이터원 레퍼런스

설계 문서(04·05·06·09·11)가 참조하는 외부 서비스 명세·제약·역할의 단일 참조처. **키 값은 `.env`에만 두고, 여기엔 변수명만 적는다.**

## 1. 서비스 목록

| 서비스 | 받는 것 | 키(.env) | 우선순위 |
|---|---|---|---|
| **KIS Developers** | 국내 시세·공매도(과거 포함)·수급(최근30일)·계좌·주문, 해외 시세 | `KIS_*` (실전·모의) | P0 |
| **Anthropic (Claude)** | 뉴스 판단·매매 결정(LLM) | `ANTHROPIC_API_KEY` | P0 |
| **네이버 검색** | 뉴스 헤드라인 | `NAVER_CLIENT_ID/SECRET` | P0 |
| **네이버 금융**(스크래핑) | **과거 수급(외국인·기관)·상폐/과거 시세**(백테스트) | 없음 | P0(백테스트) |
| **yfinance** | 글로벌 지수·환율·VIX·SOX | 없음 | P0 |
| **DART** | 전자공시 | `DART_API_KEY` | P1 |
| **FRED** | 美 매크로 | `FRED_API_KEY` | P1 |
| **healthchecks.io** | 데드맨 스위치 | `HEALTHCHECK_URL` | P1 |
| **Discord 웹훅** | 알림 | `DISCORD_WEBHOOK_URL` | P1 |

> **KRX·pykrx 폐기(2026-06-13 실측)**: KRX 정보데이터시스템(`data.krx.co.kr/.../getJsonData.cmd`)은 정책 변경으로 자동 호출 시 전부 `LOGOUT`(HTTP 400) 차단 → 직접 스크래핑 불가. pykrx도 같은 원인으로 깨짐. KRX OPEN API엔 수급/공매도 없음. → **과거 수급·상폐시세는 네이버 금융으로 대체**(공매도는 KIS로 이미 대체). KRX·pykrx 의존 전면 제거.

## 2. 데이터원 역할 분담 (Phase 0 실측, 2026-06)

평소 **운영은 KIS 단일 소스**. 네이버 금융은 백테스트 데이터를 **1회 수집·DB 적재**할 때만 쓰는 보조다.

| 데이터 | 출처 |
|---|---|
| 일봉 OHLCV(운영·최근), 계좌·주문 | **KIS** |
| 공매도(운영·**과거 모두**) | **KIS** — 2018년치까지 실측(★) |
| 실시간 수급(최근 30거래일) | **KIS** |
| 과거 수급(백테스트, 외국인·기관) | **네이버 금융** frgn (★★) |
| 상폐 종목·과거 시세(생존편향 차단) | **네이버 금융** sise_day (★★) |
| 글로벌 지수·환율 | **yfinance** |

★ **2026-06-11 KIS 실측**(005930): 수급 `inquire-investor`는 최근 **30거래일만**(과거 불가). 공매도 `daily-short-sale`은 `FID_INPUT_DATE`로 **2018·2020년 구간도 정상**(82건/4개월) → 공매도는 KIS만으로 백테스트 가능.

★★ **2026-06-13 네이버 실측**: §4 참조. 과거 수급 8년+(2010 이전까지), 상폐종목(한진해운) 상폐직전까지 OHLCV 정상.

## 3. KIS 명세

- **도메인**: 실전 `openapi.koreainvestment.com:9443` / 모의 `openapivts.koreainvestment.com:29443`
- **토큰**: `/oauth2/tokenP`, 24h 만료. **발급은 1분당 1회 제한**(`EGW00133`, 실측) → 파일 캐싱·재사용(매 호출 재발급 금지)
- **Rate limit**: 연속 호출 시 "초당 거래건수 초과(rt_cd=1)" → 조회 루프에 **0.5~0.6초 간격**(특히 모의)
- **일봉**: 한 호출 최대 ~100건 → 5~10년치는 구간 페이지네이션

| 용도 | 엔드포인트 | TR_ID (실전 / 모의) |
|---|---|---|
| 현재가 | `quotations/inquire-price` | `FHKST01010100` |
| 기간별 일봉 | `quotations/inquire-daily-itemchartprice` | `FHKST03010100` (수정주가 `FID_ORG_ADJ_PRC`) |
| 투자자 수급 | `quotations/inquire-investor` | `FHKST01010900` (최근 30거래일만, 실측 → 과거는 네이버) |
| 공매도 일별 | `quotations/daily-short-sale` | `FHPST04830000` (`FID_INPUT_DATE_1/2`로 과거 구간 가능, ~100건/호출, 실측) |
| 잔고 | `trading/inquire-balance` | `TTTC8434R` / `VTTC8434R` |
| 현금 주문 | `trading/order-cash` | `TTTC0802U`·`TTTC0801U` / `VTTC0802U`·`VTTC0801U` |
| 일별주문체결 | `trading/inquire-daily-ccld` | `TTTC0081R` / `VTTC0081R` (송출실패 시 접수확인, 11-2.3) |
| 정정·취소 | `trading/order-rvsecncl` | `TTTC0803U` / `VTTC0803U` |

**ORD_DVSN(주문구분)**: 00 지정가 · 01 시장가 · 02 조건부지정가 · 03 최유리 · 04 최우선 · 05~07 시간외 · 11~16 IOC/FOK · 21 중간가 · **22 스톱지정가** · 23·24 중간가 IOC/FOK
- 진입은 **11 IOC지정가**(11-2.8), 스톱지정가(22) = `ORD_DVSN=22` + `CNDT_PRIC`(트리거가) + `ORD_UNPR`(발동 지정가) (05-risk 5-2)

**모의 도메인 시세 지원**(2026-06-11 실측): 모의 도메인도 현재가·일봉·수급·공매도 4종 정상 → **시세도 모드 도메인·모드 토큰으로 통일**(실전 도메인 고정 불필요). 모드 분기는 broker `_PROFILES` 데이터로만.

**모의 미지원**(실측·명세): 스톱지정가(22) — `"제공하지 않는 주문유형"`(2026-06-10) / 실현손익·기간손익·매매손익 조회 / 신용·예약주문. → 손절·KPI는 실전 소액(Phase 7.5)에서 검증(05-risk 5.2, 06-data 304).

## 4. 네이버 금융 (스크래핑) — 백테스트 과거 데이터

공식 API 아님. 공개 웹페이지(HTML 표)를 직접 파싱. **키 불필요**, `User-Agent` 헤더 필요, 인코딩 **euc-kr**, `pandas.read_html`(lxml). 종목은 **단축코드 6자리**(KIS와 동일, ISIN 변환 불필요).

| 데이터 | URL | 페이지당 | 컬럼 |
|---|---|---|---|
| 과거 수급 | `finance.naver.com/item/frgn.naver?code={코드}&page={N}` | ~20거래일 | 날짜·종가·거래량·**기관순매매량·외국인순매매량**·외국인보유주수·외국인보유율 |
| 과거/상폐 시세 | `finance.naver.com/item/sise_day.naver?code={코드}&page={N}` | 10거래일 | 날짜·시가·고가·저가·종가·거래량 |

- **깊이**(2026-06-13 실측, 005930): frgn 2010 이전까지, sise_day 상폐종목(한진해운 117930) 상폐직전(2017-03)까지 OHLCV 정상.
- **제약**: 개인 순매수는 직접 미제공(외국인·기관만 — 수급 신호 핵심엔 충분). 페이지당 소량 → 종목당 수십~100요청, **rate limit(요청 간 지연)·1회 수집 후 DB 캐싱 필수**. 비공식 경로라 구조 변경 시 깨질 수 있음(파서 격리·실패 알림).
- 잠정↔확정: 수급은 장중잠정→장후확정, 공매도·대차 T+2~3 지연 → 백테스트는 잠정/확정 라벨로 미래정보 누설 차단(04-data ④).

## 5. `.env` 변수명

```dotenv
KIS_APP_KEY=  KIS_APP_SECRET=  KIS_ACCOUNT_NO=            # 실전(계좌 8자리-2자리)
KIS_PAPER_APP_KEY=  KIS_PAPER_APP_SECRET=  KIS_PAPER_ACCOUNT_NO=   # 모의
ANTHROPIC_API_KEY=
NAVER_CLIENT_ID=  NAVER_CLIENT_SECRET=
DART_API_KEY=  FRED_API_KEY=
HEALTHCHECK_URL=  DISCORD_WEBHOOK_URL=
# 네이버 금융·yfinance는 키 없음(스크래핑) / 운영 파라미터는 config/*.toml
```

## 6. vintage·모의 지원 매트릭스 (04-data 4.2 참조, 실측값)

| 항목 | 현황 | 처리 |
|---|---|---|
| KIS 일봉 과거 | 12년+(~100건/호출) | 페이지네이션 |
| KIS 수정주가 | 제공(액면분할 보정 확인) | 제공값 우선 |
| KIS 투자자 수급 | 최근 30거래일만(실측) | 과거는 네이버 금융 |
| KIS 공매도 | 과거 가능(2018치 실측) | 구간 페이지네이션 |
| KIS 토큰 발급 | 1분당 1회(실측) | 파일 캐싱 재사용 |
| KIS 스톱22·손익조회 | 모의 미지원 | 실전 검증(5.2) |
| 네이버 과거수급 | 외국인·기관 8년+(실측) | 1회 수집·DB 적재, 개인 미제공 |
| 네이버 상폐/과거시세 | 상폐직전까지 OHLCV(실측) | 생존편향 차단 |
| yfinance | silent 사후수정, 백업 Stooq는 지수·금리만 | vintage 동결, ADR은 KIS 해외주식 대체(09-tech 31) |
| FRED | 美10Y·기준금리=일별, GDP·CPI=대폭수정 | GDP·CPI 도입 시 ALFRED |
