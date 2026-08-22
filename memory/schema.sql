-- ALphaLoop SQLite 스키마 — docs/07-data-model.md 표 카탈로그의 구현(단일 소스).
--
-- 공통 규칙(07-model):
--   · 시각은 TEXT ISO8601 UTC / 거래일(tradeDate)은 KST 기준 YYYY-MM-DD
--   · 가격·손익·비율 REAL, 수량·건수 INTEGER, 참/거짓 INTEGER(0,1)
--   · 백테스트 결과는 DB에 넣지 않는다(tune_results/ 파일). 모의·실전은 mode 열로 구분
--   · 조회 속도용 인덱스는 걸지 않는다 — 실제로 느려진 뒤 추가. 유일성은 전부 기본키가 담당
--
-- 표 17개: 시장 데이터 7 · 판단 6 · 집행 3 · 감사 1 (07-model 표 카탈로그와 1:1)

PRAGMA user_version = 2;

-- ════════════════════════════════════════════════════════════
-- 6.1 시장 데이터 — 장 시작 전 일일 배치가 적재(전 종목 대상)
-- ════════════════════════════════════════════════════════════

-- 종목 명부. KIS 종목마스터(.mst) 파일 2개를 받아 통째로 덮어쓴다.
CREATE TABLE IF NOT EXISTS Symbols (
    symbolId           TEXT PRIMARY KEY,          -- 종목코드(6자리)
    name               TEXT NOT NULL,
    market             TEXT NOT NULL CHECK(market IN ('KOSPI','KOSDAQ')),
    securityType       TEXT NOT NULL CHECK(securityType IN ('common','preferred','spac','reit','etf','etn')),
    listedDate         TEXT,                      -- 상장 경과일 제외 필터
    delistedDate       TEXT,                      -- NULL이면 상장 중
    dartCorpCode       TEXT,                      -- DART 회사코드(기업행위 조회)
    lastUpdateDateTime TEXT NOT NULL
);

-- 날짜별 매매 제약 지정 상태. 같은 마스터 파일에서 뽑되 딱지가 붙은 종목만 적재하고,
-- 표에 없으면 정상으로 본다. 날짜별로 쌓는 이유는 백테스트의 룩어헤드 차단(07-model 6.1).
CREATE TABLE IF NOT EXISTS SymbolStates (
    symbolId          TEXT NOT NULL REFERENCES Symbols(symbolId),
    tradeDate         TEXT NOT NULL,
    isHalted          INTEGER NOT NULL DEFAULT 0 CHECK(isHalted IN (0,1)),
    isAdmin           INTEGER NOT NULL DEFAULT 0 CHECK(isAdmin IN (0,1)),
    isWarning         INTEGER NOT NULL DEFAULT 0 CHECK(isWarning IN (0,1)),
    isOverheated      INTEGER NOT NULL DEFAULT 0 CHECK(isOverheated IN (0,1)),
    collectedDateTime TEXT NOT NULL,
    PRIMARY KEY (symbolId, tradeDate)
);

-- 전 종목 일봉. 매일 어제 확정분 한 줄씩 추가(종목당 1호출).
CREATE TABLE IF NOT EXISTS DailyBars (
    symbolId         TEXT NOT NULL REFERENCES Symbols(symbolId),
    tradeDate        TEXT NOT NULL,
    open             REAL,
    high             REAL,                        -- ATR 계산
    low              REAL,                        -- ATR 계산
    close            REAL NOT NULL,               -- 점수 4개 항목의 입력
    volume           INTEGER,
    value            REAL,                        -- 거래대금
    adjustmentFactor REAL NOT NULL DEFAULT 1.0,   -- 누적 보정 배수(원본 복원용)
    isAdjusted       INTEGER NOT NULL DEFAULT 0 CHECK(isAdjusted IN (0,1)),
    PRIMARY KEY (symbolId, tradeDate)
);

-- 외국인·기관 순매수. KIS inquire-investor(최근 30거래일)로 어제분 적재.
CREATE TABLE IF NOT EXISTS DailyFlows (
    symbolId          TEXT NOT NULL REFERENCES Symbols(symbolId),
    tradeDate         TEXT NOT NULL,
    foreignNet        REAL,
    institutionNet    REAL,
    isFinal           INTEGER NOT NULL DEFAULT 0 CHECK(isFinal IN (0,1)),  -- 잠정치는 0
    collectedDateTime TEXT NOT NULL,
    PRIMARY KEY (symbolId, tradeDate)
);

-- 권리락 등 기업행위. DART 정형 API(fricDecsn·crDecsn·piicDecsn)에서 기준일·배수를 받는다.
-- 배당락은 정형 API가 없어 현재 미수집(04-data 4.1).
CREATE TABLE IF NOT EXISTS CorporateActions (
    actionId          TEXT PRIMARY KEY,
    symbolId          TEXT NOT NULL REFERENCES Symbols(symbolId),
    exDate            TEXT NOT NULL,             -- 신주배정기준일·감자기준일
    actionType        TEXT NOT NULL CHECK(actionType IN ('bonus','rights','reduction','split','merger','dividend')),
    priceFactor       REAL,                      -- 손절선 조정 비율
    detail            TEXT,                      -- 배정비율·감자비율 원자료
    source            TEXT NOT NULL,
    collectedDateTime TEXT NOT NULL
);

-- 코스피·코스닥 지수. 벤치마크 비교(11장 게이트)의 유일한 근거이자 레짐 라벨의 원천.
-- 레짐은 결정 입력으로 쓰지 않고 사후 분류 축으로만 쓴다(04-data).
CREATE TABLE IF NOT EXISTS MarketIndices (
    indexCode         TEXT NOT NULL CHECK(indexCode IN ('KOSPI','KOSDAQ')),
    tradeDate         TEXT NOT NULL,
    close             REAL NOT NULL,
    sma200            REAL,
    regime            TEXT CHECK(regime IN ('uptrend','downtrend')),
    collectedDateTime TEXT NOT NULL,
    PRIMARY KEY (indexCode, tradeDate)
);

-- 배치 실행 1회의 결과. 5단계 데이터 신선도 검사의 판정 근거이자 재실행 시 이어받기 기준.
CREATE TABLE IF NOT EXISTS IngestRuns (
    runId            TEXT PRIMARY KEY,
    targetTable      TEXT NOT NULL,
    source           TEXT NOT NULL,
    rangeStartDate   TEXT,
    rangeEndDate     TEXT,
    status           TEXT NOT NULL CHECK(status IN ('ok','partial','failed')),
    targetCount      INTEGER,
    successCount     INTEGER,
    rowsWritten      INTEGER,
    errorMessage     TEXT,
    startedDateTime  TEXT NOT NULL,
    finishedDateTime TEXT
);

-- ════════════════════════════════════════════════════════════
-- 6.2 판단 — 사이클이 계산한 것
-- ════════════════════════════════════════════════════════════

-- 사이클 1회의 실행 기록. status가 중복 주문 방지의 근거(12-ops 11-2.1) —
-- ordering에서 죽었으면 KIS 주문 조회로 실제 송출 여부를 먼저 확인한다.
CREATE TABLE IF NOT EXISTS Cycles (
    cycleId          TEXT PRIMARY KEY,           -- 시각 기반 발급. 모든 산출물의 부모 키
    tradeDate        TEXT NOT NULL,
    status           TEXT NOT NULL CHECK(status IN ('intent','scoring','deciding','ordering','recorded','failed','skipped')),
    skipReason       TEXT,
    failedStep       INTEGER,
    mode             TEXT NOT NULL,
    startedDateTime  TEXT NOT NULL,
    finishedDateTime TEXT
);

-- 사이클 시점의 계좌 총액. 4단계 사이징의 분모이며, 시계열이라야 낙폭을 잴 수 있어 쌓는다.
CREATE TABLE IF NOT EXISTS AccountSnapshots (
    snapshotId       TEXT PRIMARY KEY,
    cycleId          TEXT NOT NULL REFERENCES Cycles(cycleId),
    tradeDate        TEXT NOT NULL,
    amount           REAL NOT NULL,              -- 예수금
    positionValue    REAL NOT NULL,              -- 보유 평가금액(거래정지 종목은 동결가)
    totalAsset       REAL NOT NULL,
    dayStartAsset    REAL,                       -- 일일 −4% 판정의 분모(5.1)
    dayReturnPercent REAL,
    recordedDateTime TEXT NOT NULL
);

-- 하루 1회 산출하는 전 종목 점수. 원시값과 백분위를 함께 저장해야
-- 나중에 가중치를 바꿔 재계산할 수 있다(백분위는 그날 통과 집합에 의존).
CREATE TABLE IF NOT EXISTS DailyScores (
    tradeDate               TEXT NOT NULL,
    symbolId                TEXT NOT NULL REFERENCES Symbols(symbolId),
    passedFilter            INTEGER NOT NULL CHECK(passedFilter IN (0,1)),  -- 백분위 모집단 기준
    filterReason            TEXT,
    momentum                REAL,                -- 12-1 모멘텀 원시값
    flowNet5Day             REAL,
    flowNet20Day            REAL,
    valueRatio              REAL,                -- 거래대금 5일/60일
    isTrendAligned          INTEGER CHECK(isTrendAligned IN (0,1)),
    volatility              REAL,                -- 60일 실현변동성
    momentumPercentile      REAL,                -- 가중치 0.35
    flowPercentile          REAL,                -- 0.25
    valuePercentile         REAL,                -- 0.15
    trendPercentile         REAL,                -- 0.10
    lowVolatilityPercentile REAL,                -- 0.15
    totalScore              REAL,
    rank                    INTEGER,             -- 1단계 상위 N 컷의 기준
    computedDateTime        TEXT NOT NULL,
    PRIMARY KEY (tradeDate, symbolId)
);

-- 워치리스트 종목의 사이클 시점 값. 이 표가 곧 워치리스트다(별도 표 없음).
CREATE TABLE IF NOT EXISTS CycleScores (
    cycleId             TEXT NOT NULL REFERENCES Cycles(cycleId),
    symbolId            TEXT NOT NULL REFERENCES Symbols(symbolId),
    inclusion           TEXT NOT NULL CHECK(inclusion IN ('topRank','surge','holding')),
    baseScore           REAL,                    -- DailyScores의 전일 기준 점수
    trendPercentileLive REAL,                    -- 장중 갱신되는 두 항목
    flowPercentileLive  REAL,
    totalScore          REAL,                    -- 진입·무효 임계 판정의 값
    lastPrice           REAL,
    buyQuantity         INTEGER,                 -- 매수 1호가 잔량(점하한가 판정)
    sellQuantity        INTEGER,                 -- 매도 1호가 잔량(점상한가 판정)
    atr                 REAL,
    stopWidth           REAL,                    -- max(2.5 × ATR, 현재가 × 5%)
    isTradable          INTEGER CHECK(isTradable IN (0,1)),
    blockReason         TEXT CHECK(blockReason IN ('limitUp','limitDown','halted','vi','overheated')),
    scoredDateTime      TEXT NOT NULL,
    PRIMARY KEY (cycleId, symbolId)
);

-- 4단계가 낸 제안 주문. 5단계 게이트가 거부·축소할 수 있으므로 확정이 아니다.
-- 점수 미달 무거래는 남기지 않는다(CycleScores로 유추 가능) — costExceedsEdge만 남긴다.
CREATE TABLE IF NOT EXISTS Decisions (
    decisionId      TEXT PRIMARY KEY,
    cycleId         TEXT NOT NULL REFERENCES Cycles(cycleId),
    symbolId        TEXT NOT NULL REFERENCES Symbols(symbolId),
    action          TEXT NOT NULL CHECK(action IN ('buy','exitAll','raiseStop','noTrade')),
    reason          TEXT NOT NULL CHECK(reason IN ('entryThreshold','thesisInvalid','stopHit','timeExit','breakeven','trail','costExceedsEdge')),
    score           REAL,
    threshold       REAL,
    entryPrice      REAL,
    stopPrice       REAL,                        -- 무효화선
    riskPerShare    REAL,                        -- R. 청산까지 고정
    winRate         REAL,                        -- 켈리 입력(초기엔 NULL — 위험비율 1% 상한 고정)
    payoffRatio     REAL,
    riskPercent     REAL,                        -- min(1%, 0.25 × 켈리분수)
    quantity        INTEGER,
    rewardRiskRatio REAL,
    estimatedCost   REAL,
    netEdge         REAL,                        -- 음수면 무거래
    regime          TEXT,                        -- MarketIndices에서 복사한 라벨
    decidedDateTime TEXT NOT NULL
);

-- 5단계 게이트 판정. 여러 규칙이 동시에 걸려도 가장 먼저 걸린 하나만 사유로 남긴다(5.2).
CREATE TABLE IF NOT EXISTS RiskChecks (
    checkId         TEXT PRIMARY KEY,
    cycleId         TEXT NOT NULL REFERENCES Cycles(cycleId),
    decisionId      TEXT REFERENCES Decisions(decisionId),  -- 사이클 단위 검사(1~4번)는 NULL
    checkOrder      INTEGER NOT NULL,            -- 5.2 검사 순서 = 심각도
    checkName       TEXT NOT NULL CHECK(checkName IN ('balanceSync','marketHalt','dataFreshness','circuitBreaker','schema','hardLimit','symbolState')),
    result          TEXT NOT NULL CHECK(result IN ('pass','reject','reduce','skipCycle','safeStop')),
    reason          TEXT,
    limitValue      REAL,                        -- 한도와 실측을 나란히 두면 초과폭 집계가 된다
    actualValue     REAL,
    checkedDateTime TEXT NOT NULL
);

-- ════════════════════════════════════════════════════════════
-- 6.3 집행
-- ════════════════════════════════════════════════════════════

-- KIS에 보낸 주문. 기본키가 clientOrderId인 것이 중복 주문 방지의 핵심 —
-- 같은 의도면 재시작 후에도 같은 값이 나와 두 번째 삽입이 거부된다.
CREATE TABLE IF NOT EXISTS Orders (
    clientOrderId    TEXT PRIMARY KEY,           -- {cycleId}-{symbolId}-{side}-{seq}
    cycleId          TEXT REFERENCES Cycles(cycleId),      -- 상주 스톱 자동 체결은 NULL
    decisionId       TEXT REFERENCES Decisions(decisionId),
    kisOrderNo       TEXT,                       -- 정정·취소에 필요
    symbolId         TEXT NOT NULL REFERENCES Symbols(symbolId),
    side             TEXT NOT NULL CHECK(side IN ('buy','sell')),
    purpose          TEXT NOT NULL CHECK(purpose IN ('entry','stop','stopAmend','exit')),
    orderType        TEXT NOT NULL,              -- 00 지정가 · 11 IOC · 22 스톱지정가
    orderQuantity    INTEGER NOT NULL,
    orderPrice       REAL,
    triggerPrice     REAL,                       -- stop·stopAmend만
    filledQuantity   INTEGER NOT NULL DEFAULT 0,
    averageFillPrice REAL,
    fee              REAL,
    tax              REAL,
    slippageEstimate REAL,
    status           TEXT NOT NULL CHECK(status IN ('submitted','partial','filled','cancelled','rejected')),
    orderedDateTime  TEXT NOT NULL,
    filledDateTime   TEXT,
    mode             TEXT NOT NULL               -- cycleId가 NULL일 수 있어 따로 둔다
);

-- 보유 상태. 15개 표 중 유일하게 덮어쓰는 표(변경 이력은 Orders로 되짚는다).
-- KIS 실잔고와 대조하는 우리 측 기록이자 진입 결정↔청산 결과를 잇는 다리.
CREATE TABLE IF NOT EXISTS Positions (
    positionId        TEXT PRIMARY KEY,
    symbolId          TEXT NOT NULL REFERENCES Symbols(symbolId),
    market            TEXT,                      -- 청산 비용 산정 기준
    quantity          INTEGER NOT NULL,
    averagePrice      REAL NOT NULL,
    entryDecisionId   TEXT REFERENCES Decisions(decisionId),
    entryDate         TEXT,                      -- 보유일수·시간 기반 청산 기준
    initialStopPrice  REAL,                      -- R 고정 기준. 청산까지 불변
    currentStopPrice  REAL,                      -- 트레일링·본전 상향으로 변동
    riskPerShare      REAL,
    isBreakevenDone   INTEGER NOT NULL DEFAULT 0 CHECK(isBreakevenDone IN (0,1)),
    activeStopOrderId TEXT REFERENCES Orders(clientOrderId),  -- 비면 손절 없이 방치된 포지션
    status            TEXT NOT NULL CHECK(status IN ('open','closed','frozen')),
    frozenDateTime    TEXT,
    frozenPrice       REAL,                      -- 정지 직전 가격(자본곡선 왜곡 방지)
    frozenReason      TEXT,
    openedDateTime    TEXT NOT NULL,
    updatedDateTime   TEXT
);

-- 청산 실현손익. 진입 시 점수·레짐을 함께 박아두므로 보정통계 전용 표가 필요 없다.
CREATE TABLE IF NOT EXISTS Outcomes (
    outcomeId        TEXT PRIMARY KEY,
    positionId       TEXT REFERENCES Positions(positionId),
    entryDecisionId  TEXT REFERENCES Decisions(decisionId),
    exitDecisionId   TEXT REFERENCES Decisions(decisionId),  -- 상주 스톱 자동 체결은 NULL
    symbolId         TEXT NOT NULL REFERENCES Symbols(symbolId),
    entryPrice       REAL,
    exitPrice        REAL,
    quantity         INTEGER,
    entryDate        TEXT,
    exitDate         TEXT,
    holdingDays      INTEGER,
    grossProfitLoss  REAL,
    fee              REAL,
    tax              REAL,
    netProfitLoss    REAL,                       -- 성과 집계는 이 값만 쓴다
    returnPercent    REAL,
    rMultiple        REAL,                       -- 손익 ÷ (R × 수량)
    exitReason       TEXT CHECK(exitReason IN ('stopHit','timeExit','thesisInvalid','trail')),
    entryScore       REAL,                       -- 학습의 원천 ↓
    entryScoreBucket INTEGER,                    -- 보정통계의 집계 축
    entryRegime      TEXT,
    closedDateTime   TEXT NOT NULL,
    mode             TEXT NOT NULL
);

-- ════════════════════════════════════════════════════════════
-- 6.4 감사
-- ════════════════════════════════════════════════════════════

-- 매매 전체 정지의 발생·해제. releasedDateTime이 비어 있으면 지금 정지 중이라는 뜻이고,
-- 사이클 시작 시 이 상태를 조회해 신규 주문을 차단한다(보유 청산은 계속 돈다).
CREATE TABLE IF NOT EXISTS SafeStopEvents (
    eventId          TEXT PRIMARY KEY,
    cycleId          TEXT REFERENCES Cycles(cycleId),
    occurredDateTime TEXT NOT NULL,
    cause            TEXT NOT NULL,
    trigger          TEXT NOT NULL CHECK(trigger IN ('auto','manual')),
    releasedDateTime TEXT,
    releasedBy       TEXT,                       -- 잔고 불일치·데이터 오류는 사람 개입 필수(5.3)
    releaseReason    TEXT
);
