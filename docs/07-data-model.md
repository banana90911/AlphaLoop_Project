## 7. 데이터 모델
**저장소** : PostgreSQL DB 1개(`journal`). 매매 코어와 **같은 서버 안**에서 돌고, 바깥에는 포트를 열지 않는다.

**왜 파일 DB(SQLite)가 아닌가**
매매 코어(쓰기)와 대시보드 API(읽기)가 서로 다른 프로세스로 같은 데이터를 다루고, 대시보드가 절대 쓰지 못한다는 보장을 DB 계정 권한으로 강제할 수 있기 때문이다. 관리할 것이 파일 하나에서 서비스 하나로 늘어나는 대신, 금액 컬럼의 타입 강제와 스키마 변경 도구를 함께 얻는다.

**공통 규칙**
- 기본키는 `*_id`(`text`), 발급 책임은 그 행을 만드는 코드에 있다. 시장 데이터처럼 자연키가 뚜렷한 표는 `(symbolId, tradeDate)` 복합키를 쓴다.
- 시각은 `timestamptz`로 UTC 저장. 일별 집계용 거래일(`tradeDate`)은 KST 기준 `date`로 따로 둔다.
- **금액·가격·손익은 `numeric`** — 반올림 오차가 자본곡선에 누적되면 안 되기 때문이다. 비율·점수·지표는 `double precision`, 수량·건수는 `integer`, 참/거짓은 `boolean`.
- **접속 계정은 둘** — 매매 코어용(읽기·쓰기)과 대시보드용(`SELECT`만). 아래 표 카탈로그 전체에 이 구분이 적용된다.
- **백테스트 결과는 DB에 넣지 않는다.** 실거래 표에 `source='backtest'`로 섞지 않으므로, 조건을 빼먹어 백테스트 성과가 실적으로 집계되는 사고가 원천적으로 없다(백테스트 산출물은 파일 — 9장).
- 모의(paper)와 실전(live)은 같은 표에 `mode` 열로 구분한다.

**표 카탈로그**


| 묶음     | 표                  | 한 줄 성격             | 시점  |
| ------ | ------------------ | ------------------ | --- |
| 시장 데이터 | `Symbols`          | 종목 마스터             | 초기  |
| 〃      | `SymbolStates`     | 날짜별 매매 제약 지정 상태    | 초기  |
| 〃      | `DailyBars`        | 전 종목 일봉·거래대금       | 초기  |
| 〃      | `DailyFlows`       | 외국인·기관 순매수         | 초기  |
| 〃      | `CorporateActions` | 권리락 등 기업행위         | 초기  |
| 〃      | `MarketIndices`    | 코스피·코스닥 지수·레짐 라벨   | 초기  |
| 〃      | `IngestRuns`       | 배치 실행 이력·결측 추적     | 초기  |
| 판단     | `Cycles`           | 사이클 1회의 상태 머신      | 초기  |
| 〃      | `AccountSnapshots` | 사이클 시점 자본 스냅샷      | 초기  |
| 〃      | `DailyScores`      | 하루 1회 전 종목 점수(1단계) | 초기  |
| 〃      | `CycleScores`      | 워치리스트 갱신 점수(3단계)   | 초기  |
| 〃      | `Decisions`        | 제안 주문 1건           | 초기  |
| 〃      | `RiskChecks`       | 게이트 판정 1건          | 초기  |
| 집행     | `Orders`           | 주문 송출 1건           | 초기  |
| 〃      | `Positions`        | 보유 상태              | 초기  |
| 〃      | `Outcomes`         | 청산 실현손익            | 초기  |
| 감사     | `SafeStopEvents`   | 전체 정지 발생·해제        | 초기  |

---

### 7.1 시장 데이터
`Symbols`
> - 종목 데이터
> - 배치로 zip 파일을 받아와 적재
> - 1단계 제외 필터에서 사용

| 컬럼                   | 의미                                             |
| -------------------- | ---------------------------------------------- |
| `symbolId` (PK)      | 종목코드                                           |
| `name`               | 종목명                                            |
| `market`             | `KOSPI`/`KOSDAQ`                               |
| `securityType`       | `common`/`preferred`/`spac`/`reit`/`etf`/`etn` |
| `listedDate`         | 상장일                                            |
| `delistedDate`       | 상장폐지일                                          |
| `dartCorpCode`       | DART 회사코드 — 종목코드와 체계가 달라 기업행위 조회에 필요           |
| `lastUpdateDateTime` | 갱신 시각                                          |


`SymbolStates`
> - 날짜별 매매 제약 지정 상태
> - 배치로 zip 파일을 받아와 적재
> - 1단계 제외 필터에서 사용

| 컬럼                  | 의미      |
| ------------------- | ------- |
| `symbolId` (PK)     | 종목코드    |
| `tradeDate` (PK)    | 거래일     |
| `isHalted`          | 거래정지    |
| `isAdmin`           | 관리종목    |
| `isWarning`         | 투자경고·위험 |
| `isOverheated`      | 단기과열 지정 |
| `collectedDateTime` | 수집 시각   |


`DailyBars`
> - 전 종목 일봉·거래대금. 점수·지표 계산의 1차 입력
> - 배치로 API 조회를 통해 적재
> - 3단계 ATR 계산에 사용

| 컬럼                 | 의미            |
| ------------------ | ------------- |
| `symbolId` (PK)    | 종목코드          |
| `tradeDate` (PK)   | 거래일           |
| `open`             | 시가            |
| `high`             | 고가            |
| `low`              | 저가            |
| `close`            | 종가            |
| `volume`           | 거래량           |
| `value`            | 거래대금          |
| `adjustmentFactor` | 누적 보정 배수      |
| `isAdjusted`       | 기업행위 보정 적용 여부 |


`DailyFlows`
> - 외국인·기관 순매수. 수급 점수(가중치 0.25)의 입력
> - 배치로 종목별 API 조회를 통해 적재
> - 1단계 수급 점수 산출에 사용

| 컬럼                  | 의미      |
| ------------------- | ------- |
| `symbolId` (PK)     | 종목코드    |
| `tradeDate` (PK)    | 거래일     |
| `foreignNet`        | 외국인 순매수 |
| `institutionNet`    | 기관 순매수  |
| `isFinal`           | 확정치 여부  |
| `collectedDateTime` | 수집 시각   |


`CorporateActions`
> - 권리락 등 기업행위
> - 배치로 DART 공시목록을 훑어 기업행위 공시를 찾고, 해당 회사의 정형 API로 기준일과 배수를 받아 적재
> - 3단계에서 보유 종목 손절선을 같은 비율로 조정, 4단계에서 KIS 스톱 주문 정정에 사용

| 컬럼                  | 의미                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------ |
| `actionId` (PK)     |                                                                                      |
| `symbolId`          | 종목코드                                                                                 |
| `exDate`            | 권리락·배당락일                                                                             |
| `actionType`        | `bonus`(무상증자)/`rights`(유상증자)/`reduction`(감자·병합)/`split`(분할)/`merger`/`dividend`(미수집) |
| `priceFactor`       | 가격 조정 배수(전일 종가 → 당일 기준가) — 손절선 조정 비율                                                 |
| `detail`            | 배정비율·감자비율 등 원자료                                                                      |
| `source`            | 출처(`dart`)                                                                           |
| `collectedDateTime` | 수집 시각                                                                                |


`MarketIndices`
> - 지수 데이터
> - 배치로 지수 2개만 조회해 적재
> - 벤치마크 비교

| 컬럼                  | 의미                    |
| ------------------- | --------------------- |
| `indexCode` (PK)    | `KOSPI`/`KOSDAQ`      |
| `tradeDate` (PK)    | 거래일                   |
| `close`             | 지수 종가                 |
| `sma200`            | 200일 이동평균             |
| `regime`            | `uptrend`/`downtrend` |
| `collectedDateTime` | 수집 시각                 |


`IngestRuns`
> - 배치 실행 1회의 결과. 배치가 잘 돌았는지의 기록
> - 배치가 각 대상 표를 채울 때마다 스스로 한 줄씩 남긴다
> - 4단계 데이터 신선도 검사에서 사용

| 컬럼                 | 의미                            |
| ------------------ | ----------------------------- |
| `runId` (PK)       |                               |
| `targetTable`      | 대상 표                          |
| `source`           | 출처 어댑터명                       |
| `rangeStartDate`   | 수집 대상 기간 시작(일일 배치는 종료일과 같음)   |
| `rangeEndDate`     | 수집 대상 기간 종료                   |
| `status`           | `ok`/`partial`/`failed`       |
| `targetCount`      | 조회 대상 건수(예: 종목 2,558)         |
| `successCount`     | 성공 건수 — `partial` 판정과 이어받기 기준 |
| `rowsWritten`      | 실제 적재된 행 수                    |
| `errorMessage`     | 실패 사유                         |
| `startedDateTime`  | 시작 시각                         |
| `finishedDateTime` | 종료 시각 — 신선도 판정의 기준            |


---

### 7.2 판단
`Cycles`
> - 사이클 1회의 실행 기록
> - 사이클 시작 시 한 줄 생성하고, 단계가 넘어갈 때마다 `status`를 갱신
> - 사이클 시작 전 직전 사이클이 `recorded`인지 확인하고, `ordering`에서 죽었으면 KIS 주문 조회로 실제 송출 여부를 먼저 확인


| 컬럼                 | 의미                                                                                                         |
| ------------------ | ---------------------------------------------------------------------------------------------------------- |
| `cycleId` (PK)     | 시각 기반 발급. 모든 사이클 산출물의 부모 키                                                                                 |
| `tradeDate`        | 거래일                                                                                                        |
| `status`           | `intent`→`scoring`(1~2단계)→`deciding`(3~4단계)→`ordering`(5단계)→`recorded`(6단계), 실패 시 `failed`, 건너뜀은 `skipped` |
| `skipReason`       | 휴장·시장 마비·SafeStop 등 스킵 사유                                                                                  |
| `failedStep`       | 실패한 단계 번호                                                                                                  |
| `mode`             | `dev`/`prd`                                                                                                |
| `startedDateTime`  | 시작 시각                                                                                                      |
| `finishedDateTime` | 종료 시각                                                                                                      |


`AccountSnapshots`
> - 사이클 시점의 자본
> - 사이클마다 한 줄씩 추가
> - 3단계 사이징(위험금액의 분모), 4단계 총노출 한도 검사, 대시보드 당일 손익률에 사용

| 컬럼                 | 의미              |
| ------------------ | --------------- |
| `snapshotId` (PK)  |                 |
| `cycleId` (FK)     |                 |
| `tradeDate`        |                 |
| `amount`           | 예수금             |
| `positionValue`    | 보유 평가금액         |
| `totalAsset`       | 총자본 = 현금 + 평가금액 |
| `dayStartAsset`    | 당일 첫 사이클의 총자본   |
| `dayReturnPercent` | 당일 손익률          |
| `recordedDateTime` |                 |


`DailyScores`
> - 하루 1회 산출하는 전 종목 점수
> - 일일 배치의 마지막 단계에서 산출
> - 1단계 워치리스트를 구성

| 컬럼                        | 의미                                      |
| ------------------------- | --------------------------------------- |
| `tradeDate` (PK)          | 거래일                                     |
| `symbolId` (PK)           | 종목코드                                    |
| `passedFilter`            | 제외·거래현실성 필터 통과 여부                       |
| `filterReason`            | 탈락 사유(우선주·상장 60일 미만·관리종목·동전주·거래대금 미달 등) |
| `momentum`                | 12-1 모멘텀 원시값 — 20거래일 전 ÷ 252거래일 전 − 1   |
| `flowNet20Day`            | 외국인·기관 20일 누적 순매수                       |
| `valueRatio`              | 거래대금 5일 평균 ÷ 60일 평균                     |
| `volatility`              | 60일 실현변동성                               |
| `momentumPercentile`      | 모멘텀 백분위 (가중치 0.35)                      |
| `flowPercentile`          | 수급 백분위 (0.25)                           |
| `valuePercentile`         | 거래대금 증가 백분위 (0.15)                      |
| `lowVolatilityPercentile` | 저변동성 백분위 (0.15)                         |
| `totalScore`              | 가중합 종합점수 — 비중 합 0.90을 1.0으로 재정규화        |
| `rank`                    | 종합점수 순위 — 1단계 상위 N 컷의 기준                |
| `computedDateTime`        | 산출 시각                                   |


`CycleScores`
> - 워치리스트 종목의 사이클 시점 값
> - 1단계에서 워치리스트를 확정해 생성, 2단계에서 KIS 실시간 조회 결과 적재
> - 3단계에서 진입·무효 임계를 판정, 4단계 종목 상태 게이트와 5단계 송출 직전 호가 확인에 사용
> - 네 항목 중 장중에 움직이는 것은 수급 잠정치뿐이라 점수는 사실상 전일 값 그대로다(4-2). 사이클 시점 시세는 진입가·손절가 확정과 거래 가능 여부 판정에 쓴다

| 컬럼                    | 의미                                                           |
| --------------------- | ------------------------------------------------------------ |
| `cycleId` (PK)        | 사이클                                                          |
| `symbolId` (PK)       | 종목코드                                                         |
| `inclusion`           | 편입 사유 — `topRank`/`surge`(당일 급등)/`holding`(보유)               |
| `baseScore`           | `DailyScores`의 전일 기준 종합점수                                    |
| `flowPercentileLive`  | 장중 잠정 수급 백분위 — 20일 누적이라 당일 하루 몫이 작다                          |
| `totalScore`          | 임계 판정에 쓴 종합점수                                                |
| `lastPrice`           | 사이클 시점 현재가                                                   |
| `buyQuantity`         | 매수 1호가 잔량                                                    |
| `sellQuantity`        | 매도 1호가 잔량                                                    |
| `atr`                 | 손절폭 산출용 ATR                                                  |
| `stopWidth`           | `2.0 × ATR` — R 산정 기준                                        |
| `isTradable`          | 지금 거래 가능한 상태인가                                               |
| `blockReason`         | `limitUp`/`limitDown`/`halted`/`vi`/`overheated` 중 어느 것에 걸렸나 |
| `scoredDateTime`      | 산출 시각                                                        |


`Decisions`
> - 3단계가 낸 제안 주문 1건
> - 3단계에서 생성
> - 4단계 리스크 검증, 5단계 Orders로 송출, 6단계 성과 귀속

| 컬럼                | 의미                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------- |
| `decisionId` (PK) |                                                                                             |
| `cycleId` (FK)    |                                                                                             |
| `symbolId`        | 종목코드                                                                                        |
| `action`          | `buy`/`exitAll`(전량 청산)/`raiseStop`(트레일링·본전 상향)/`noTrade`. `exitPartial`(부분 청산)은 규칙에서 뺐다(6-2) |
| `reason`          | `entryThreshold`/`thesisInvalid`/`stopHit`/`timeExit`/`breakeven`/`trail`/`costExceedsEdge` |
| `score`           | 판정에 쓴 갱신 점수                                                                                 |
| `threshold`       | 그때 적용된 임계값(진입 임계 또는 무효 임계)                                                                  |
| `entryPrice`      | 진입 기준가(사이클 시점 현재가)                                                                          |
| `stopPrice`       | 무효화선 — 틀렸다고 인정할 가격                                                                          |
| `riskPerShare`    | R = 진입가 − 최초 손절가. 청산까지 고정되는 기준자                                                             |
| `targetPositions` | 그 시점 목표 보유 종목 수 — 동일가중 배분의 분모                                                               |
| `quantity`        | 제안 수량. 신규는 총자본 ÷ `targetPositions` ÷ 진입가, 1주 단위 내림(6-1)                                     |
| `rewardRiskRatio` | 손익비                                                                                         |
| `estimatedCost`   | 왕복 거래비용 추정(거래세·수수료·슬리피지)                                                                    |
| `netEdge`         | 비용을 뺀 기대 엣지. 음수면 무거래                                                                        |
| `regime`          | 사이클 시점 시장 레짐                                                                                |
| `decidedDateTime` | 판정 시각                                                                                       |


`RiskChecks`
> - 4단계 게이트 판정 1건. 검사 7개를 5.2의 고정 순서로 수행한 결과
> - 4단계에서 적재. 여러 규칙이 동시에 걸려도 **가장 먼저 걸린 하나만** 사유로 남긴다
> - 5단계는 `pass`인 결정만 송출한다. 어느 게이트가 자주 걸리는지가 한도 조정의 근거

| 컬럼                | 의미                                                                                             |
| ----------------- | ---------------------------------------------------------------------------------------------- |
| `checkId` (PK)    |                                                                                                |
| `cycleId` (FK)    |                                                                                                |
| `decisionId` (FK) | 결정 단위 검사(5~7번)일 때만. 사이클 단위 검사(1~4번)는 NULL                                                      |
| `checkOrder`      | 5.2 검사 순서 번호(1~7) — 어느 관문에서 걸렸는지                                                               |
| `checkName`       | `balanceSync`/`marketHalt`/`dataFreshness`/`circuitBreaker`/`schema`/`hardLimit`/`symbolState` |
| `result`          | `pass`/`reject`/`reduce`(수량 축소)/`skipCycle`/`safeStop`                                         |
| `reason`          | 단일 사유                                                                                          |
| `limitValue`      | 총노출 기준                                                                                         |
| `actualValue`     | 실측값(예: 제안이 자본의 27%) — 한도와 나란히 두면 초과폭 집계가 가능                                                    |
| `checkedDateTime` | 판정 시각                                                                                          |

---

### 7.3 집행
`Orders`
> - KIS에 보낸 주문
> - 5단계 송출 직후 생성, 체결 확인 시 체결 갱신
> - 4단계 미체결 주문 정리와 상주 스톱의 주문번호 조회, 5단계 ****중복 주문 필터

| 컬럼                   | 의미                                                                                            |
| -------------------- | --------------------------------------------------------------------------------------------- |
| `clientOrderId` (PK) | `{cycleId}-{symbolId}-{side}-{seq}` — 같은 의도면 항상 같은 값이 나오므로, 재시작 후 재송출이 삽입 단계에서 거부된다(중복 주문 방지) |
| `cycleId` (FK)       | 상주 스톱의 자동 체결은 NULL                                                                            |
| `decisionId` (FK)    | 〃                                                                                             |
| `kisOrderNo`         | KIS 주문번호 — 정정·취소에 필요                                                                          |
| `symbolId`           | 종목코드                                                                                          |
| `side`               | `buy`/`sell`                                                                                  |
| `purpose`            | `entry`(신규 진입)/`stop`(손절 예약 등록)/`stopAmend`(스톱 정정)/`exit`(능동 매도 — 부분·전량 공통)                    |
| `orderType`          | KIS 주문 구분 — `00` 지정가·`11` IOC지정가·`22` 스톱지정가                                                   |
| `orderQuantity`      | 주문 수량                                                                                         |
| `orderPrice`         | 주문 가격                                                                                         |
| `triggerPrice`       | 스톱 발동가(`stop`·`stopAmend`만)                                                                   |
| `filledQuantity`     | 체결 수량                                                                                         |
| `averageFillPrice`   | 평균 체결가                                                                                        |
| `fee`                | 수수료                                                                                           |
| `tax`                | 거래세(매도만)                                                                                      |
| `slippageEstimate`   | 주문가와 체결가의 차이                                                                                  |
| `status`             | `submitted`/`partial`/`filled`/`cancelled`/`rejected`                                         |
| `orderedDateTime`    | 송출 시각                                                                                         |
| `filledDateTime`     | 체결 시각                                                                                         |
| `mode`               | 모의/실전 — `cycleId`가 NULL일 수 있어 따로 둔다                                                           |


`Positions`
> - 종목 보유 상태
> - 진입 체결 시 적재, 부분 청산 시 `quantity` 차감, 전량 청산 시 `status='closed'`
> - 잔고대조에 사용

| 컬럼                       | 의미                                              |
| ------------------------ | ----------------------------------------------- |
| `positionId` (PK)        |                                                 |
| `symbolId`               | 종목코드                                            |
| `market`                 | `KOSPI`/`KOSDAQ` — 청산 비용 산정 기준                  |
| `quantity`               | 보유 수량                                           |
| `averagePrice`           | 평단가                                             |
| `entryDecisionId` (FK)   | 이 포지션을 연 결정                                     |
| `entryDate`              | 진입일 — 보유일수·시간 기반 청산 기준                          |
| `initialStopPrice`       | 최초 손절가. R 고정 기준이라 **청산까지 불변**                   |
| `currentStopPrice`       | 현재 KIS에 상주하는 스톱 발동가(트레일링·본전 상향으로 변동)            |
| `riskPerShare`           | R = `averagePrice` − `initialStopPrice`         |
| `isBreakevenDone`        | +1.5R 본전 상향 완료 여부(중복 실행 방지)                      |
| `activeStopOrderId` (FK) | 현재 상주 중인 스톱 주문 — 정정·취소 대상. 비어 있으면 손절 없이 방치된 포지션 |
| `status`                 | `open`/`closed`/`frozen`(거래정지 동결)               |
| `frozenDateTime`         | 동결 시점                                           |
| `frozenPrice`            | 동결 평가가 — 정지 직전 가격                               |
| `frozenReason`           | 거래정지 / 관리종목 / 투자경고·위험                           |
| `openedDateTime`         | 최초 진입 시각                                        |
| `updatedDateTime`        | 마지막 갱신 시각                                       |


`Outcomes`
> - 청산 체결 1건의 실현손익. **부분 청산도 한 행이다**(부분 매도 → 1행, 잔여 청산 → 1행). 부분 익절은 현재 쓰지 않으므로(`partial_frac = 0`, 06-sizing 6.2) 한 포지션당 1행이 기본이다
> - 6단계 청산이 체결될 때마다 적재
> - 3단계 켈리 승률/손익비

| 컬럼                     | 의미                                           |
| ---------------------- | -------------------------------------------- |
| `outcomeId` (PK)       |                                              |
| `positionId` (FK)      |                                              |
| `entryDecisionId` (FK) | 진입 결정                                        |
| `exitDecisionId` (FK)  | 청산 결정. 상주 스톱 자동 체결은 NULL                     |
| `symbolId`             | 종목코드                                         |
| `entryPrice`           | 진입 체결가                                       |
| `exitPrice`            | 청산 체결가                                       |
| `quantity`             | 이번 청산 수량(부분 청산이면 그 일부)                       |
| `entryDate`            | 진입일                                          |
| `exitDate`             | 청산일                                          |
| `holdingDays`          | 진입일부터 이번 청산일까지                               |
| `grossProfitLoss`      | 비용 차감 전 손익                                   |
| `fee`                  | 수수료(매수·매도 합)                                 |
| `tax`                  | 거래세                                          |
| `netProfitLoss`        | 비용 차감 후 손익 — **성과 집계는 이 값만 쓴다**              |
| `returnPercent`        | 수익률                                          |
| `rMultiple`            | 손익 ÷ (R × `quantity`) — 종목·금액이 달라도 비교되는 공통 잣대 |
| `exitKind`             | `partial`(부분 청산)/`full`(잔여 전량) — 성과 집계 축     |
| `exitReason`           | `breakeven`(+1.5R 부분 익절)/`stopHit`/`timeExit`/`thesisInvalid`/`trail` |
| `entryScore`           | 진입 시 종합점수                                    |
| `entryScoreBucket`     | 그 점수의 구간 — 보정통계의 집계 축                        |
| `entryRegime`          | 진입 시 레짐                                      |
| `closedDateTime`       | 청산 완료 시각                                     |
| `mode`                 | 모의/실전                                        |

**부분 청산이 성과 집계를 왜곡하지 않게** — 한 포지션이 `Outcomes` 두 행(부분·잔여)으로 나뉘므로, 행을 그대로 세면 승률이 부풀려진다. +1.5R 부분 익절은 정의상 항상 이익이라 "이긴 거래"가 한 건 더 생기는 셈이다. 그래서 집계 규칙을 둘로 나눈다.

- **켈리 입력(승률·손익비)** — `positionId`로 묶어 **포지션 단위**로 합산한 뒤 센다. 부분 익절이 이익을 남기고 잔여가 손절돼도 그 포지션은 한 건이다.
- **거래 품질 진단**(청산 사유별 분포·평균 R 등) — 행 단위로 보되 `exitKind`로 나눠 본다.

---

### 7.4 감사·학습
`SafeStopEvents`
> - 매매 정지 데이터
> - 4단계 safeStop일 때 적재
> - 4단계 사이클 시작 시 미해제 이벤트가 있으면 신규 주문 차단, 감사

| 컬럼                 | 의미                                    |
| ------------------ | ------------------------------------- |
| `eventId` (PK)     |                                       |
| `cycleId` (FK)     | 연관 사이클                                |
| `occurredDateTime` | 발생 시점                                 |
| `cause`            | 잔고 불일치 / 데이터 오류 / API 오류율 급증 / 이상행동      |
| `trigger`          | `auto`/`manual`                       |
| `releasedDateTime` | 해제 시점. **비어 있으면 지금 정지 중**             |
| `releasedBy`       | 해제자 — 잔고 불일치·데이터 오류는 사람 개입 필수(5.4)    |
| `releaseReason`    | 해제 사유                                 |