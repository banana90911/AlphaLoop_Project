# 외부 API·데이터원 레퍼런스

설계 문서(04·05·06·09·11)가 참조하는 외부 서비스 명세·제약·역할의 단일 참조처. **키 값은 `.env`에만 두고, 여기엔 변수명만 적는다.**

## 1. 서비스 목록

| 서비스 | 받는 것 | 키(.env) | 우선순위 |
|---|---|---|---|
| **KIS Developers** | 국내 시세·공매도(과거 포함)·수급(최근30일)·계좌·주문, 해외 시세 | `KIS_*` (실전·모의) | P0 |
| **Anthropic (Claude)** | 뉴스 판단·매매 결정(LLM) | `ANTHROPIC_API_KEY` | P0 |
| **네이버 검색** | 뉴스 헤드라인 | `NAVER_CLIENT_ID/SECRET` | P0 |
| **yfinance** | 글로벌 지수·환율·VIX·SOX | 없음 | P0 |
| **KRX 정보데이터시스템**(스크래핑) | **과거 수급**(백테스트, 공매도는 KIS로 대체) | 없음 | P0(백테스트) |
| **pykrx** | 상폐 종목 과거 시세 | 없음 | P1(백테스트) |
| **KRX OPEN API** | 과거 가격·종목정보(보조, 2010~) — *수급/공매도 없음* | `KRX_API_KEY` | P2 |
| **DART** | 전자공시 | `DART_API_KEY` | P1 |
| **FRED** | 美 매크로 | `FRED_API_KEY` | P1 |
| **healthchecks.io** | 데드맨 스위치 | `HEALTHCHECK_URL` | P1 |
| **Discord 웹훅** | 알림 | `DISCORD_WEBHOOK_URL` | P1 |

## 2. 데이터원 역할 분담 (Phase 0 실측, 2026-06)

KIS는 거래·실시간·최근엔 완벽하나 **백테스트용 과거 시계열·상폐종목은 안 준다.**

| 데이터 | 출처 |
|---|---|
| 일봉 OHLCV(운영·최근), 계좌·주문 | **KIS** |
| 공매도(운영·**과거 모두**) | **KIS** — 2018년치까지 실측 확인(아래 ★) |
| 실시간 수급(최근 30거래일) | **KIS** |
| 과거 수급(백테스트, 30일 초과) | **KRX 정보데이터시스템 직접 스크래핑** (data.krx.co.kr) |
| 상폐 종목 과거 시세(생존편향 차단) | **pykrx** |
| 글로벌 지수·환율 | **yfinance** |

★ **2026-06-11 실측**(005930, 실전): 수급 `inquire-investor`는 최근 **30거래일만**(과거 불가) → 과거 수급은 KRX. 공매도 `daily-short-sale`은 `FID_INPUT_DATE`로 **2018·2020년 구간도 정상 반환**(82건/4개월) → **공매도는 KIS만으로 백테스트 가능, KRX 불필요.** 한 호출 ~100건 → 구간 페이지네이션.

## 3. KIS 명세

- **도메인**: 실전 `openapi.koreainvestment.com:9443` / 모의 `openapivts.koreainvestment.com:29443`
- **토큰**: `/oauth2/tokenP`, 24h 만료. **발급은 1분당 1회 제한**(`EGW00133`, 2026-06-11 실측) → 반드시 파일 캐싱·재사용(매 호출 재발급 금지)
- **Rate limit**: 연속 호출 시 "초당 거래건수 초과(rt_cd=1)" → 조회 루프에 **0.5~0.6초 간격**(특히 모의)
- **일봉**: 한 호출 최대 ~100건 → 5~10년치는 구간 페이지네이션

| 용도 | 엔드포인트 | TR_ID (실전 / 모의) |
|---|---|---|
| 현재가 | `quotations/inquire-price` | `FHKST01010100` |
| 기간별 일봉 | `quotations/inquire-daily-itemchartprice` | `FHKST03010100` (수정주가 `FID_ORG_ADJ_PRC`) |
| 투자자 수급 | `quotations/inquire-investor` | `FHKST01010900` (최근 30거래일만, 실측 → 과거는 KRX) |
| 공매도 일별 | `quotations/daily-short-sale` | `FHPST04830000` (`FID_INPUT_DATE_1/2`로 과거 구간 가능, ~100건/호출, 실측) |
| 잔고 | `trading/inquire-balance` | `TTTC8434R` / `VTTC8434R` |
| 현금 주문 | `trading/order-cash` | `TTTC0802U`·`TTTC0801U` / `VTTC0802U`·`VTTC0801U` |
| 정정·취소 | `trading/order-rvsecncl` | `TTTC0803U` / `VTTC0803U` |

**ORD_DVSN(주문구분)**: 00 지정가 · 01 시장가 · 02 조건부지정가 · 03 최유리 · 04 최우선 · 05~07 시간외 · 11~16 IOC/FOK · 21 중간가 · **22 스톱지정가** · 23·24 중간가 IOC/FOK
- 스톱지정가(22) = `ORD_DVSN=22` + `CNDT_PRIC`(트리거가) + `ORD_UNPR`(발동 지정가) (05-risk 5-2)

**모의 미지원**(실측·명세): 스톱지정가(22) — `"제공하지 않는 주문유형"`(2026-06-10) / 실현손익·기간손익·매매손익 조회 / 신용·예약주문. → 손절·KPI는 실전 소액(Phase 7.5)에서 검증(05-risk 5.2, 06-data 304).

## 4. KRX — 과거 수급·공매도·가격

과거 백테스트 데이터는 KRX의 두 시스템으로 나뉜다. **핵심(수급·공매도)은 OPEN API가 아니라 정보데이터시스템에 있다.**

**(A) KRX 정보데이터시스템 (스크래핑) — 과거 수급의 출처** (공매도는 KIS로 대체되어 불필요)
- `data.krx.co.kr/comm/bldAttendant/getJsonData.cmd`, **키 불필요**. 투자자별 거래실적이 여기 있다(OPEN API엔 없음).
- 호출: `bld` 코드 + 헤더 `Referer: http://data.krx.co.kr/`. **정확한 bld는 구현 시 브라우저 네트워크 분석으로 확정**(추측 호출 금지).
- pykrx도 이 시스템을 긁지만 수급/공매도 함수가 노후로 깨짐 → **직접 호출 구현 필요**(데이터 레이어 과제). OHLCV·상폐는 pykrx로 동작하므로 원천 차단은 아님.

**(B) KRX OPEN API — 가격·종목정보 보조**
- `openapi.krx.co.kr` (키 `KRX_API_KEY`), base `data-dbg.krx.co.kr/svc/apis`, 헤더 `AUTH_KEY`(공백 strip), 일 10,000건.
- 제공: **일별매매·종목기본정보·지수만**(수급/공매도 없음 — 서비스 목록 확인). 키 발급과 별개로 **서비스별 이용신청** 필요, 미신청 시 401.
- 가격은 KIS로 충분하므로 보조 위치. `.env`: `KRX_API_KEY`.

## 5. pykrx

스크래핑(키 없음). **상폐 종목 과거 시세 가능**(한진해운 검증). 수급·공매도 함수는 깨짐(KRX 변경 미대응, User-Agent 주입해도 안 됨) → 과거 수급/공매도는 KRX 정보데이터시스템 직접 호출(4-A).

## 6. `.env` 변수명

```dotenv
KIS_APP_KEY=  KIS_APP_SECRET=  KIS_ACCOUNT_NO=            # 실전(계좌 8자리-2자리)
KIS_PAPER_APP_KEY=  KIS_PAPER_APP_SECRET=  KIS_PAPER_ACCOUNT_NO=   # 모의
ANTHROPIC_API_KEY=
NAVER_CLIENT_ID=  NAVER_CLIENT_SECRET=
DART_API_KEY=  FRED_API_KEY=  KRX_API_KEY=
HEALTHCHECK_URL=  DISCORD_WEBHOOK_URL=
# yfinance·pykrx는 키 없음 / 운영 파라미터는 config/*.toml
```

## 7. vintage·모의 지원 매트릭스 (04-data 4.2 참조, 실측값)

| 항목 | 현황 | 처리 |
|---|---|---|
| KIS 일봉 과거 | 12년+(~100건/호출) | 페이지네이션 |
| KIS 수정주가 | 제공(액면분할 보정 확인) | 제공값 우선 |
| KIS 투자자 수급 | 최근 30거래일만(실측) | 과거는 KRX |
| KIS 공매도 | 과거 가능(2018치 실측, ~100건/호출) | 구간 페이지네이션, **KRX 불필요** |
| KIS 토큰 발급 | 1분당 1회(실측) | 파일 캐싱 재사용 |
| KIS 스톱22·손익조회 | 모의 미지원 | 실전 검증(5.2) |
| pykrx 수급/공매도 | 깨짐 | KRX 정보데이터시스템 직접 호출(4-A) |
| KRX OPEN API | 수급/공매도 미제공(가격·종목정보만) | 가격은 KIS로, 보조만 |
| pykrx 상폐 시세 | 가용 | 생존편향 차단 |
| yfinance | silent 사후수정, 백업 Stooq는 지수·금리만(VIX·DXY·SOX·원자재·ADR 백업 불확실) | vintage 동결, ADR은 KIS 해외주식 대체(09-tech 31) |
| FRED | 美10Y·기준금리=일별(vintage 작음), GDP·CPI=대폭수정 | GDP·CPI 도입 시 ALFRED |
| KRX 잠정↔확정 | 수급 장중잠정→장후확정, 공매도·대차 T+2~3 | 잠정/확정 라벨로 누설 차단 |
