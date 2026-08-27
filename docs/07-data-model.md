## 7. 데이터 모델
**저장소** : PostgreSQL DB 1개(`journal`). 매매 코어와 **같은 서버 안**에서 돌고, 바깥에는 포트를 열지 않는다.

**왜 파일 DB(SQLite)가 아닌가**
매매 코어(쓰기)와 대시보드 API(읽기)가 서로 다른 프로세스로 같은 데이터를 다루고, 대시보드가 절대 쓰지 못한다는 보장을 DB 계정 권한으로 강제할 수 있기 때문이다. 관리할 것이 파일 하나에서 서비스 하나로 늘어나는 대신, 금액 컬럼의 타입 강제와 스키마 변경 도구를 함께 얻는다.

**공통 규칙**
- 기본키는 `*Id`(`text`), 발급 책임은 그 행을 만드는 코드에 있다. 시장 데이터처럼 자연키가 뚜렷한 표는 `(SymbolId, TradeDate)` 복합키를 쓴다.
- 시각은 `timestamptz`로 UTC 저장. 일별 집계용 거래일(`TradeDate`)은 KST 기준 `date`로 따로 둔다.
- **금액·가격·손익은 `numeric`** — 반올림 오차가 자본곡선에 누적되면 안 되기 때문이다. 비율·점수·지표는 `double precision`, 수량·건수는 `integer`, 참/거짓은 `boolean`.
- **접속 계정은 둘** — 매매 코어용(읽기·쓰기)과 대시보드용(`SELECT`만). 아래 표 카탈로그 전체에 이 구분이 적용된다.
- **백테스트 결과는 DB에 넣지 않는다.** 실거래 표에 `Source='backtest'`로 섞지 않으므로, 조건을 빼먹어 백테스트 성과가 실적으로 집계되는 사고가 원천적으로 없다(백테스트 산출물은 파일 — 9장).
- 모의(paper)와 실전(live)은 같은 표에 `Mode` 열로 구분한다.

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
| `SymbolId` (PK)      | 종목코드                                           |
| `Name`               | 종목명                                            |
| `Market`             | `KOSPI`/`KOSDAQ`                               |
| `SecurityType`       | `common`/`preferred`/`spac`/`reit`/`etf`/`etn` |
| `ListedDate`         | 상장일                                            |
| `DelistedDate`       | 상장폐지일                                          |
| `DartCorpCode`       | DART 회사코드 — 종목코드와 체계가 달라 기업행위 조회에 필요           |
| `LastUpdateDateTime` | 갱신 시각                                          |


`SymbolStates`
> - 날짜별 매매 제약 지정 상태
> - 배치로 zip 파일을 받아와 적재
> - 1단계 제외 필터에서 사용

| 컬럼                  | 의미      |
| ------------------- | ------- |
| `SymbolId` (PK)     | 종목코드    |
| `TradeDate` (PK)    | 거래일     |
| `IsHalted`          | 거래정지    |
| `IsAdmin`           | 관리종목    |
| `IsWarning`         | 투자경고·위험 |
| `IsOverheated`      | 단기과열 지정 |
| `CollectedDateTime` | 수집 시각   |


`DailyBars`
> - 전 종목 일봉·거래대금. 점수·지표 계산의 1차 입력
> - 배치로 API 조회를 통해 적재
> - 3단계 ATR 계산에 사용

| 컬럼                 | 의미            |
| ------------------ | ------------- |
| `SymbolId` (PK)    | 종목코드          |
| `TradeDate` (PK)   | 거래일           |
| `Open`             | 시가            |
| `High`             | 고가            |
| `Low`              | 저가            |
| `Close`            | 종가            |
| `Volume`           | 거래량           |
| `Value`            | 거래대금          |
| `AdjustmentFactor` | 누적 보정 배수      |
| `IsAdjusted`       | 기업행위 보정 적용 여부 |


`DailyFlows`
> - 외국인·기관 순매수. 수급 점수(가중치 0.25)의 입력
> - 배치로 종목별 API 조회를 통해 적재
> - 1단계 수급 점수 산출에 사용

| 컬럼                  | 의미      |
| ------------------- | ------- |
| `SymbolId` (PK)     | 종목코드    |
| `TradeDate` (PK)    | 거래일     |
| `ForeignNet`        | 외국인 순매수 |
| `InstitutionNet`    | 기관 순매수  |
| `IsFinal`           | 확정치 여부  |
| `CollectedDateTime` | 수집 시각   |


`CorporateActions`
> - 권리락 등 기업행위
> - 배치로 DART 공시목록을 훑어 기업행위 공시를 찾고, 해당 회사의 정형 API로 기준일과 배수를 받아 적재
> - 3단계에서 보유 종목 손절선을 같은 비율로 조정, 4단계에서 KIS 스톱 주문 정정에 사용

| 컬럼                  | 의미                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------ |
| `ActionId` (PK)     |                                                                                      |
| `SymbolId`          | 종목코드                                                                                 |
| `ExDate`            | 권리락·배당락일                                                                             |
| `ActionType`        | `bonus`(무상증자)/`rights`(유상증자)/`reduction`(감자·병합)/`split`(분할)/`merger`/`dividend`(미수집) |
| `PriceFactor`       | 가격 조정 배수(전일 종가 → 당일 기준가) — 손절선 조정 비율                                                 |
| `Detail`            | 배정비율·감자비율 등 원자료                                                                      |
| `Source`            | 출처(`dart`)                                                                           |
| `CollectedDateTime` | 수집 시각                                                                                |


`MarketIndices`
> - 지수 데이터
> - 배치로 지수 2개만 조회해 적재
> - 벤치마크 비교

| 컬럼                  | 의미                    |
| ------------------- | --------------------- |
| `IndexCode` (PK)    | `KOSPI`/`KOSDAQ`      |
| `TradeDate` (PK)    | 거래일                   |
| `Close`             | 지수 종가                 |
| `Sma200`            | 200일 이동평균             |
| `Regime`            | `uptrend`/`downtrend` |
| `CollectedDateTime` | 수집 시각                 |


`IngestRuns`
> - 배치 실행 1회의 결과. 배치가 잘 돌았는지의 기록
> - 배치가 각 대상 표를 채울 때마다 스스로 한 줄씩 남긴다
> - 4단계 데이터 신선도 검사에서 사용

| 컬럼                 | 의미                            |
| ------------------ | ----------------------------- |
| `RunId` (PK)       |                               |
| `TargetTable`      | 대상 표                          |
| `Source`           | 출처 어댑터명                       |
| `RangeStartDate`   | 수집 대상 기간 시작(일일 배치는 종료일과 같음)   |
| `RangeEndDate`     | 수집 대상 기간 종료                   |
| `Status`           | `ok`/`partial`/`failed`       |
| `TargetCount`      | 조회 대상 건수(예: 종목 2,558)         |
| `SuccessCount`     | 성공 건수 — `partial` 판정과 이어받기 기준 |
| `RowsWritten`      | 실제 적재된 행 수                    |
| `ErrorMessage`     | 실패 사유                         |
| `StartedDateTime`  | 시작 시각                         |
| `FinishedDateTime` | 종료 시각 — 신선도 판정의 기준            |


---

### 7.2 판단
`Cycles`
> - 사이클 1회의 실행 기록
> - 사이클 시작 시 한 줄 생성하고, 단계가 넘어갈 때마다 `Status`를 갱신
> - 사이클 시작 전 직전 사이클이 `recorded`인지 확인하고, `ordering`에서 죽었으면 KIS 주문 조회로 실제 송출 여부를 먼저 확인


| 컬럼                 | 의미                                                                                                         |
| ------------------ | ---------------------------------------------------------------------------------------------------------- |
| `CycleId` (PK)     | 시각 기반 발급. 모든 사이클 산출물의 부모 키                                                                                 |
| `TradeDate`        | 거래일                                                                                                        |
| `Status`           | `intent`→`scoring`(1~2단계)→`deciding`(3~4단계)→`ordering`(5단계)→`recorded`(6단계), 실패 시 `failed`, 건너뜀은 `skipped` |
| `SkipReason`       | 휴장·시장 마비·SafeStop 등 스킵 사유                                                                                  |
| `FailedStep`       | 실패한 단계 번호                                                                                                  |
| `Mode`             | `dev`/`prd`                                                                                                |
| `StartedDateTime`  | 시작 시각                                                                                                      |
| `FinishedDateTime` | 종료 시각                                                                                                      |


`AccountSnapshots`
> - 사이클 시점의 자본
> - 사이클마다 한 줄씩 추가
> - 3단계 사이징(위험금액의 분모), 4단계 총노출 한도 검사, 대시보드 당일 손익률에 사용

| 컬럼                 | 의미              |
| ------------------ | --------------- |
| `SnapshotId` (PK)  |                 |
| `CycleId` (FK)     |                 |
| `TradeDate`        |                 |
| `Amount`           | 예수금             |
| `PositionValue`    | 보유 평가금액         |
| `TotalAsset`       | 총자본 = 현금 + 평가금액 |
| `DayStartAsset`    | 당일 첫 사이클의 총자본   |
| `DayReturnPercent` | 당일 손익률          |
| `RecordedDateTime` |                 |


`DailyScores`
> - 하루 1회 산출하는 전 종목 점수
> - 일일 배치의 마지막 단계에서 산출
> - 1단계 워치리스트를 구성

| 컬럼                        | 의미                                      |
| ------------------------- | --------------------------------------- |
| `TradeDate` (PK)          | 거래일                                     |
| `SymbolId` (PK)           | 종목코드                                    |
| `PassedFilter`            | 제외·거래현실성 필터 통과 여부                       |
| `FilterReason`            | 탈락 사유(우선주·상장 60일 미만·관리종목·동전주·거래대금 미달 등) |
| `Momentum`                | 12-1 모멘텀 원시값 — 20거래일 전 ÷ 252거래일 전 − 1   |
| `FlowNet20Day`            | 외국인·기관 20일 누적 순매수                       |
| `ValueRatio`              | 거래대금 5일 평균 ÷ 60일 평균                     |
| `Volatility`              | 60일 실현변동성                               |
| `MomentumPercentile`      | 모멘텀 백분위 (가중치 0.35)                      |
| `FlowPercentile`          | 수급 백분위 (0.25)                           |
| `ValuePercentile`         | 거래대금 증가 백분위 (0.15)                      |
| `LowVolatilityPercentile` | 저변동성 백분위 (0.15)                         |
| `TotalScore`              | 가중합 종합점수 — 비중 합 0.90을 1.0으로 재정규화        |
| `Rank`                    | 종합점수 순위 — 1단계 상위 N 컷의 기준                |
| `ComputedDateTime`        | 산출 시각                                   |


`CycleScores`
> - 워치리스트 종목의 사이클 시점 값
> - 1단계에서 워치리스트를 확정해 생성, 2단계에서 KIS 실시간 조회 결과 적재
> - 3단계에서 진입·무효 임계를 판정, 4단계 종목 상태 게이트와 5단계 송출 직전 호가 확인에 사용
> - 네 항목 중 장중에 움직이는 것은 수급 잠정치뿐이라 점수는 사실상 전일 값 그대로다(4-2). 사이클 시점 시세는 진입가·손절가 확정과 거래 가능 여부 판정에 쓴다

| 컬럼                    | 의미                                                           |
| --------------------- | ------------------------------------------------------------ |
| `CycleId` (PK)        | 사이클                                                          |
| `SymbolId` (PK)       | 종목코드                                                         |
| `Inclusion`           | 편입 사유 — `topRank`/`surge`(당일 급등)/`holding`(보유)               |
| `BaseScore`           | `DailyScores`의 전일 기준 종합점수                                    |
| `FlowPercentileLive`  | 장중 잠정 수급 백분위 — 20일 누적이라 당일 하루 몫이 작다                          |
| `TotalScore`          | 임계 판정에 쓴 종합점수                                                |
| `LastPrice`           | 사이클 시점 현재가                                                   |
| `BuyQuantity`         | 매수 1호가 잔량                                                    |
| `SellQuantity`        | 매도 1호가 잔량                                                    |
| `Atr`                 | 손절폭 산출용 ATR                                                  |
| `StopWidth`           | `2.0 × ATR` — R 산정 기준                                        |
| `IsTradable`          | 지금 거래 가능한 상태인가                                               |
| `BlockReason`         | `limitUp`/`limitDown`/`halted`/`vi`/`overheated` 중 어느 것에 걸렸나 |
| `ScoredDateTime`      | 산출 시각                                                        |


`Decisions`
> - 3단계가 낸 제안 주문 1건
> - 3단계에서 생성
> - 4단계 리스크 검증, 5단계 Orders로 송출, 6단계 성과 귀속

| 컬럼                | 의미                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------- |
| `DecisionId` (PK) |                                                                                             |
| `CycleId` (FK)    |                                                                                             |
| `SymbolId`        | 종목코드                                                                                        |
| `Action`          | `buy`/`exitAll`(전량 청산)/`raiseStop`(트레일링·본전 상향)/`noTrade`. `exitPartial`(부분 청산)은 규칙에서 뺐다(6-2) |
| `Reason`          | `entryThreshold`/`thesisInvalid`/`stopHit`/`timeExit`/`breakeven`/`trail`/`costExceedsEdge` |
| `Score`           | 판정에 쓴 갱신 점수                                                                                 |
| `Threshold`       | 그때 적용된 임계값(진입 임계 또는 무효 임계)                                                                  |
| `EntryPrice`      | 진입 기준가(사이클 시점 현재가)                                                                          |
| `StopPrice`       | 무효화선 — 틀렸다고 인정할 가격                                                                          |
| `RiskPerShare`    | R = 진입가 − 최초 손절가. 청산까지 고정되는 기준자                                                             |
| `TargetPositions` | 그 시점 목표 보유 종목 수 — 동일가중 배분의 분모                                                               |
| `Quantity`        | 제안 수량. 신규는 총자본 ÷ `TargetPositions` ÷ 진입가, 1주 단위 내림(6-1)                                     |
| `RewardRiskRatio` | 손익비                                                                                         |
| `EstimatedCost`   | 왕복 거래비용 추정(거래세·수수료·슬리피지)                                                                    |
| `NetEdge`         | 비용을 뺀 기대 엣지. 음수면 무거래                                                                        |
| `Regime`          | 사이클 시점 시장 레짐                                                                                |
| `DecidedDateTime` | 판정 시각                                                                                       |


`RiskChecks`
> - 4단계 게이트 판정 1건. 검사 7개를 5.2의 고정 순서로 수행한 결과
> - 4단계에서 적재. 여러 규칙이 동시에 걸려도 **가장 먼저 걸린 하나만** 사유로 남긴다
> - 5단계는 `pass`인 결정만 송출한다. 어느 게이트가 자주 걸리는지가 한도 조정의 근거

| 컬럼                | 의미                                                                                             |
| ----------------- | ---------------------------------------------------------------------------------------------- |
| `CheckId` (PK)    |                                                                                                |
| `CycleId` (FK)    |                                                                                                |
| `DecisionId` (FK) | 결정 단위 검사(5~7번)일 때만. 사이클 단위 검사(1~4번)는 NULL                                                      |
| `CheckOrder`      | 5.2 검사 순서 번호(1~7) — 어느 관문에서 걸렸는지                                                               |
| `CheckName`       | `balanceSync`/`marketHalt`/`dataFreshness`/`circuitBreaker`/`schema`/`hardLimit`/`symbolState` |
| `Result`          | `pass`/`reject`/`reduce`(수량 축소)/`skipCycle`/`safeStop`                                         |
| `Reason`          | 단일 사유                                                                                          |
| `LimitValue`      | 총노출 기준                                                                                         |
| `ActualValue`     | 실측값(예: 제안이 자본의 27%) — 한도와 나란히 두면 초과폭 집계가 가능                                                    |
| `CheckedDateTime` | 판정 시각                                                                                          |

---

### 7.3 집행
`Orders`
> - KIS에 보낸 주문
> - 5단계 송출 직후 생성, 체결 확인 시 체결 갱신
> - 4단계 미체결 주문 정리와 상주 스톱의 주문번호 조회, 5단계 ****중복 주문 필터

| 컬럼                   | 의미                                                                                            |
| -------------------- | --------------------------------------------------------------------------------------------- |
| `ClientOrderId` (PK) | `{CycleId}-{SymbolId}-{Side}-{Seq}` — 같은 의도면 항상 같은 값이 나오므로, 재시작 후 재송출이 삽입 단계에서 거부된다(중복 주문 방지) |
| `CycleId` (FK)       | 상주 스톱의 자동 체결은 NULL                                                                            |
| `DecisionId` (FK)    | 〃                                                                                             |
| `KisOrderNo`         | KIS 주문번호 — 정정·취소에 필요                                                                          |
| `SymbolId`           | 종목코드                                                                                          |
| `Side`               | `buy`/`sell`                                                                                  |
| `Purpose`            | `entry`(신규 진입)/`stop`(손절 예약 등록)/`stopAmend`(스톱 정정)/`exit`(능동 매도 — 부분·전량 공통)                    |
| `OrderType`          | KIS 주문 구분 — `00` 지정가·`11` IOC지정가·`22` 스톱지정가                                                   |
| `OrderQuantity`      | 주문 수량                                                                                         |
| `OrderPrice`         | 주문 가격                                                                                         |
| `TriggerPrice`       | 스톱 발동가(`stop`·`stopAmend`만)                                                                   |
| `FilledQuantity`     | 체결 수량                                                                                         |
| `AverageFillPrice`   | 평균 체결가                                                                                        |
| `Fee`                | 수수료                                                                                           |
| `Tax`                | 거래세(매도만)                                                                                      |
| `SlippageEstimate`   | 주문가와 체결가의 차이                                                                                  |
| `Status`             | `submitted`/`partial`/`filled`/`cancelled`/`rejected`                                         |
| `OrderedDateTime`    | 송출 시각                                                                                         |
| `FilledDateTime`     | 체결 시각                                                                                         |
| `Mode`               | 모의/실전 — `CycleId`가 NULL일 수 있어 따로 둔다                                                           |


`Positions`
> - 종목 보유 상태
> - 진입 체결 시 적재, 부분 청산 시 `Quantity` 차감, 전량 청산 시 `status='closed'`
> - 잔고대조에 사용

| 컬럼                       | 의미                                              |
| ------------------------ | ----------------------------------------------- |
| `PositionId` (PK)        |                                                 |
| `SymbolId`               | 종목코드                                            |
| `Market`                 | `KOSPI`/`KOSDAQ` — 청산 비용 산정 기준                  |
| `Quantity`               | 보유 수량                                           |
| `AveragePrice`           | 평단가                                             |
| `EntryDecisionId` (FK)   | 이 포지션을 연 결정                                     |
| `EntryDate`              | 진입일 — 보유일수·시간 기반 청산 기준                          |
| `InitialStopPrice`       | 최초 손절가. R 고정 기준이라 **청산까지 불변**                   |
| `CurrentStopPrice`       | 현재 KIS에 상주하는 스톱 발동가(트레일링·본전 상향으로 변동)            |
| `RiskPerShare`           | R = `AveragePrice` − `InitialStopPrice`         |
| `IsBreakevenDone`        | +1.5R 본전 상향 완료 여부(중복 실행 방지)                      |
| `ActiveStopOrderId` (FK) | 현재 상주 중인 스톱 주문 — 정정·취소 대상. 비어 있으면 손절 없이 방치된 포지션 |
| `Status`                 | `open`/`closed`/`frozen`(거래정지 동결)               |
| `FrozenDateTime`         | 동결 시점                                           |
| `FrozenPrice`            | 동결 평가가 — 정지 직전 가격                               |
| `FrozenReason`           | 거래정지 / 관리종목 / 투자경고·위험                           |
| `OpenedDateTime`         | 최초 진입 시각                                        |
| `UpdatedDateTime`        | 마지막 갱신 시각                                       |


`Outcomes`
> - 청산 체결 1건의 실현손익. **부분 청산도 한 행이다**(부분 매도 → 1행, 잔여 청산 → 1행). 부분 익절은 현재 쓰지 않으므로(`partial_frac = 0`, 06-sizing 6.2) 한 포지션당 1행이 기본이다
> - 6단계 청산이 체결될 때마다 적재
> - 3단계 켈리 승률/손익비

| 컬럼                     | 의미                                           |
| ---------------------- | -------------------------------------------- |
| `OutcomeId` (PK)       |                                              |
| `PositionId` (FK)      |                                              |
| `EntryDecisionId` (FK) | 진입 결정                                        |
| `ExitDecisionId` (FK)  | 청산 결정. 상주 스톱 자동 체결은 NULL                     |
| `SymbolId`             | 종목코드                                         |
| `EntryPrice`           | 진입 체결가                                       |
| `ExitPrice`            | 청산 체결가                                       |
| `Quantity`             | 이번 청산 수량(부분 청산이면 그 일부)                       |
| `EntryDate`            | 진입일                                          |
| `ExitDate`             | 청산일                                          |
| `HoldingDays`          | 진입일부터 이번 청산일까지                               |
| `GrossProfitLoss`      | 비용 차감 전 손익                                   |
| `Fee`                  | 수수료(매수·매도 합)                                 |
| `Tax`                  | 거래세                                          |
| `NetProfitLoss`        | 비용 차감 후 손익 — **성과 집계는 이 값만 쓴다**              |
| `ReturnPercent`        | 수익률                                          |
| `RMultiple`            | 손익 ÷ (R × `Quantity`) — 종목·금액이 달라도 비교되는 공통 잣대 |
| `ExitKind`             | `partial`(부분 청산)/`full`(잔여 전량) — 성과 집계 축     |
| `ExitReason`           | `breakeven`(+1.5R 부분 익절)/`stopHit`/`timeExit`/`thesisInvalid`/`trail` |
| `EntryScore`           | 진입 시 종합점수                                    |
| `EntryScoreBucket`     | 그 점수의 구간 — 보정통계의 집계 축                        |
| `EntryRegime`          | 진입 시 레짐                                      |
| `ClosedDateTime`       | 청산 완료 시각                                     |
| `Mode`                 | 모의/실전                                        |

**부분 청산이 성과 집계를 왜곡하지 않게** — 한 포지션이 `Outcomes` 두 행(부분·잔여)으로 나뉘므로, 행을 그대로 세면 승률이 부풀려진다. +1.5R 부분 익절은 정의상 항상 이익이라 "이긴 거래"가 한 건 더 생기는 셈이다. 그래서 집계 규칙을 둘로 나눈다.

- **켈리 입력(승률·손익비)** — `PositionId`로 묶어 **포지션 단위**로 합산한 뒤 센다. 부분 익절이 이익을 남기고 잔여가 손절돼도 그 포지션은 한 건이다.
- **거래 품질 진단**(청산 사유별 분포·평균 R 등) — 행 단위로 보되 `ExitKind`로 나눠 본다.

---

### 7.4 감사·학습
`SafeStopEvents`
> - 매매 정지 데이터
> - 4단계 safeStop일 때 적재
> - 4단계 사이클 시작 시 미해제 이벤트가 있으면 신규 주문 차단, 감사

| 컬럼                 | 의미                                    |
| ------------------ | ------------------------------------- |
| `EventId` (PK)     |                                       |
| `CycleId` (FK)     | 연관 사이클                                |
| `OccurredDateTime` | 발생 시점                                 |
| `Cause`            | 잔고 불일치 / 데이터 오류 / API 오류율 급증 / 이상행동      |
| `Trigger`          | `auto`/`manual`                       |
| `ReleasedDateTime` | 해제 시점. **비어 있으면 지금 정지 중**             |
| `ReleasedBy`       | 해제자 — 잔고 불일치·데이터 오류는 사람 개입 필수(5.4)    |
| `ReleaseReason`    | 해제 사유                                 |