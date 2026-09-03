-- AlphaLoop PostgreSQL 스키마 — docs/07-data-model.md 표 카탈로그의 구현(단일 소스).
--
-- 공통 규칙(07-model):
--   · 식별자·라벨은 text. 시각은 timestamptz(UTC 저장), 거래일은 date(KST 기준)
--   · 금액·가격·손익은 numeric — 반올림 오차가 자본곡선에 누적되면 안 되기 때문
--     비율·점수·지표는 double precision, 수량·건수는 integer, 참/거짓은 boolean
--   · 컬럼·표 이름은 큰따옴표로 감싼다 — 안 감싸면 PostgreSQL이 전부 소문자로 접어
--     문서의 PascalCase와 실제 컬럼명이 어긋난다
--   · 백테스트 결과는 DB에 넣지 않는다(산출물은 파일 — 09-eval). 모의·실전은 "Mode" 열로 구분
--   · 조회 속도용 인덱스는 걸지 않는다 — 실제로 느려진 뒤 추가. 유일성은 전부 기본키가 담당
--   · 접속 계정은 둘(매매 코어=읽기·쓰기 / 대시보드=SELECT만). 권한 부여는 파일 끝 참고
--
-- 표 18개: 시장 데이터 7 · 판단 6 · 집행 4 · 감사 1 (07-model 표 카탈로그와 1:1)

-- ════════════════════════════════════════════════════════════
-- 7.1 시장 데이터 — 장 시작 전 일일 배치가 적재(전 종목 대상)
-- ════════════════════════════════════════════════════════════

-- 종목 명부. KIS 종목마스터 zip을 받아 통째로 덮어쓴다.
CREATE TABLE IF NOT EXISTS "Symbols" (
    "SymbolId"           text PRIMARY KEY,          -- 종목코드(6자리)
    "Name"               text NOT NULL,
    "Market"             text NOT NULL CHECK ("Market" IN ('KOSPI','KOSDAQ')),
    "SecurityType"       text NOT NULL
                         CHECK ("SecurityType" IN ('common','preferred','spac','reit','etf','etn')),
    "ListedDate"         date,                      -- 상장 경과일 제외 필터
    "DelistedDate"       date,                      -- NULL이면 상장 중
    "DartCorpCode"       text,                      -- DART 회사코드(기업행위 조회)
    "LastUpdateDateTime" timestamptz NOT NULL
);

-- 날짜별 매매 제약 지정 상태. 같은 마스터 파일에서 뽑되 딱지가 붙은 종목만 적재하고,
-- 표에 없으면 정상으로 본다. 날짜별로 쌓는 이유는 백테스트의 룩어헤드 차단(07-model 7.1).
CREATE TABLE IF NOT EXISTS "SymbolStates" (
    "SymbolId"          text NOT NULL REFERENCES "Symbols"("SymbolId"),
    "TradeDate"         date NOT NULL,
    "IsHalted"          boolean NOT NULL DEFAULT false,
    "IsAdmin"           boolean NOT NULL DEFAULT false,
    "IsWarning"         boolean NOT NULL DEFAULT false,
    "IsOverheated"      boolean NOT NULL DEFAULT false,
    "CollectedDateTime" timestamptz NOT NULL,
    PRIMARY KEY ("SymbolId", "TradeDate")
);

-- 전 종목 일봉. 매일 어제 확정분 한 줄씩 추가(종목당 1호출).
CREATE TABLE IF NOT EXISTS "DailyBars" (
    "SymbolId"         text NOT NULL REFERENCES "Symbols"("SymbolId"),
    "TradeDate"        date NOT NULL,
    "Open"             numeric,
    "High"             numeric,                     -- ATR 계산
    "Low"              numeric,                     -- ATR 계산
    "Close"            numeric NOT NULL,            -- 점수 4개 항목의 입력
    "Volume"           bigint,                      -- 주식수는 integer 상한을 넘길 수 있다
    "Value"            numeric,                     -- 거래대금
    "AdjustmentFactor" double precision NOT NULL DEFAULT 1.0,  -- 누적 보정 배수(원본 복원용)
    "IsAdjusted"       boolean NOT NULL DEFAULT false,
    PRIMARY KEY ("SymbolId", "TradeDate")
);

-- 외국인·기관 순매수(금액). KIS inquire-investor(최근 30거래일)로 어제분 적재.
CREATE TABLE IF NOT EXISTS "DailyFlows" (
    "SymbolId"          text NOT NULL REFERENCES "Symbols"("SymbolId"),
    "TradeDate"         date NOT NULL,
    "ForeignNet"        numeric,
    "InstitutionNet"    numeric,
    "IsFinal"           boolean NOT NULL DEFAULT false,   -- 잠정치는 false
    "CollectedDateTime" timestamptz NOT NULL,
    PRIMARY KEY ("SymbolId", "TradeDate")
);

-- 권리락 등 기업행위. DART 정형 API(fricDecsn·crDecsn·piicDecsn)에서 기준일·배수를 받는다.
-- 배당락은 정형 API가 없어 현재 미수집(04-data 4.1).
CREATE TABLE IF NOT EXISTS "CorporateActions" (
    "ActionId"          text PRIMARY KEY,
    "SymbolId"          text NOT NULL REFERENCES "Symbols"("SymbolId"),
    "ExDate"            date NOT NULL,             -- 신주배정기준일·감자기준일
    "ActionType"        text NOT NULL
                        CHECK ("ActionType" IN ('bonus','rights','reduction','split','merger','dividend')),
    "PriceFactor"       double precision,          -- 손절선 조정 비율
    "Detail"            text,                      -- 배정비율·감자비율 원자료
    "Source"            text NOT NULL,
    "CollectedDateTime" timestamptz NOT NULL
);

-- 코스피·코스닥 지수. 벤치마크 비교(09-eval 게이트)의 유일한 근거이자 레짐 라벨의 원천.
-- 레짐은 결정 입력으로 쓰지 않고 사후 분류 축으로만 쓴다(04-data).
CREATE TABLE IF NOT EXISTS "MarketIndices" (
    "IndexCode"         text NOT NULL CHECK ("IndexCode" IN ('KOSPI','KOSDAQ')),
    "TradeDate"         date NOT NULL,
    "Close"             numeric NOT NULL,
    "Sma200"            numeric,                   -- 200일 이동평균
    "Regime"            text CHECK ("Regime" IN ('uptrend','downtrend')),
    "CollectedDateTime" timestamptz NOT NULL,
    PRIMARY KEY ("IndexCode", "TradeDate")
);

-- 배치 실행 1회의 결과. 데이터 신선도 검사의 판정 근거이자 재실행 시 이어받기 기준.
CREATE TABLE IF NOT EXISTS "IngestRuns" (
    "RunId"            text PRIMARY KEY,
    "TargetTable"      text NOT NULL,
    "Source"           text NOT NULL,
    "RangeStartDate"   date,
    "RangeEndDate"     date,
    "Status"           text NOT NULL CHECK ("Status" IN ('ok','partial','failed')),
    "TargetCount"      integer,
    "SuccessCount"     integer,                    -- partial 판정과 이어받기 기준
    "RowsWritten"      integer,
    "ErrorMessage"     text,
    "StartedDateTime"  timestamptz NOT NULL,
    "FinishedDateTime" timestamptz               -- 신선도 판정의 기준
);

-- ════════════════════════════════════════════════════════════
-- 7.2 판단 — 사이클이 계산한 것
-- ════════════════════════════════════════════════════════════

-- 사이클 1회의 실행 기록. Status가 중복 주문 방지의 근거 —
-- ordering에서 죽었으면 KIS 주문 조회로 실제 송출 여부를 먼저 확인한다.
CREATE TABLE IF NOT EXISTS "Cycles" (
    "CycleId"          text PRIMARY KEY,           -- 시각 기반 발급. 모든 산출물의 부모 키
    "TradeDate"        date NOT NULL,
    "Status"           text NOT NULL
                       CHECK ("Status" IN ('intent','scoring','deciding','ordering',
                                           'recorded','failed','skipped')),
    "SkipReason"       text,
    "FailedStep"       integer,
    "Mode"             text NOT NULL,
    "StartedDateTime"  timestamptz NOT NULL,
    "FinishedDateTime" timestamptz
);

-- 사이클 시점의 계좌 총액. 사이징의 분모이며, 시계열이라야 낙폭을 잴 수 있어 쌓는다.
-- "BaseAsset"은 **직전 거래일 마지막 스냅샷의 TotalAsset**이다(05-risk 5.2 / 09-eval).
-- 예전 이름 "DayStartAsset"은 "당일 첫 사이클의 총자본"으로 읽혀서, 정기 사이클이
-- 하루 한 번인 이 시스템에서는 손익률이 항상 0%가 되는 정의였다 — 그래서 개명했다.
CREATE TABLE IF NOT EXISTS "AccountSnapshots" (
    "SnapshotId"        text PRIMARY KEY,
    "CycleId"           text NOT NULL REFERENCES "Cycles"("CycleId"),
    "TradeDate"         date NOT NULL,
    "Amount"            numeric NOT NULL,          -- 예수금
    "PositionValue"     numeric NOT NULL,          -- 보유 평가금액(거래정지 종목은 동결가)
    "TotalAsset"        numeric NOT NULL,
    "BaseAsset"         numeric,                   -- 직전 거래일 마지막 TotalAsset
    "NetFlowSinceBase"  numeric NOT NULL DEFAULT 0,  -- 기준선 이후 순외부흐름(입금 +, 출금 −)
    "AdjustedBaseAsset" numeric,                   -- BaseAsset + NetFlowSinceBase = 손익률 분모
    "CumulativeNetFlow" numeric NOT NULL DEFAULT 0,  -- 개시 이후 누적 순입금
    "TwrIndex"          double precision,          -- 시간가중수익률 지수(1.0에서 시작)
    "DayReturnPercent"  double precision,          -- TotalAsset / AdjustedBaseAsset − 1
    "RecordedDateTime"  timestamptz NOT NULL
);

-- 이미 만들어진 DB를 위한 멱등 이행. schema.sql은 매 기동마다 다시 적용되므로
-- (memory/db.py) 여기 있는 것들도 전부 여러 번 돌아도 안전해야 한다.
-- RENAME COLUMN에는 IF NOT EXISTS가 없어 존재 확인을 직접 한다.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'AccountSnapshots' AND column_name = 'DayStartAsset') THEN
        EXECUTE 'ALTER TABLE "AccountSnapshots" RENAME COLUMN "DayStartAsset" TO "BaseAsset"';
    END IF;
END
$$;

ALTER TABLE "AccountSnapshots" ADD COLUMN IF NOT EXISTS "NetFlowSinceBase"  numeric NOT NULL DEFAULT 0;
ALTER TABLE "AccountSnapshots" ADD COLUMN IF NOT EXISTS "AdjustedBaseAsset" numeric;
ALTER TABLE "AccountSnapshots" ADD COLUMN IF NOT EXISTS "CumulativeNetFlow" numeric NOT NULL DEFAULT 0;
ALTER TABLE "AccountSnapshots" ADD COLUMN IF NOT EXISTS "TwrIndex"          double precision;

-- 하루 1회 산출하는 전 종목 점수. 원시값과 백분위를 함께 저장해야
-- 나중에 가중치를 바꿔 재계산할 수 있다(백분위는 그날 통과 집합에 의존).
CREATE TABLE IF NOT EXISTS "DailyScores" (
    "TradeDate"               date NOT NULL,
    "SymbolId"                text NOT NULL REFERENCES "Symbols"("SymbolId"),
    "PassedFilter"            boolean NOT NULL,    -- 백분위 모집단 기준
    "FilterReason"            text,
    "Momentum"                double precision,    -- 12-1 모멘텀 원시값
    "FlowNet20Day"            numeric,             -- 외국인·기관 20일 누적 순매수(금액)
    "ValueRatio"              double precision,    -- 거래대금 5일/60일
    "Volatility"              double precision,    -- 60일 실현변동성
    "MomentumPercentile"      double precision,    -- 가중치 0.35
    "FlowPercentile"          double precision,    -- 0.25
    "ValuePercentile"         double precision,    -- 0.15
    "LowVolatilityPercentile" double precision,    -- 0.15
    "TotalScore"              double precision,    -- 비중 합 0.90을 1.0으로 재정규화
    "Rank"                    integer,             -- 1단계 상위 N 컷의 기준
    "ComputedDateTime"        timestamptz NOT NULL,
    PRIMARY KEY ("TradeDate", "SymbolId")
);

-- 워치리스트 종목의 사이클 시점 값. 이 표가 곧 워치리스트다(별도 표 없음).
CREATE TABLE IF NOT EXISTS "CycleScores" (
    "CycleId"            text NOT NULL REFERENCES "Cycles"("CycleId"),
    -- "Symbols" FK를 걸지 않는다: 워치리스트에는 보유 종목이 무조건 들어가는데(04-data 4.2),
    -- 상장폐지로 명부에서 빠진 보유가 기록 불가가 되면 청산 판단의 근거가 사라진다.
    -- 같은 이유로 "Decisions"·"Positions"·"Orders"의 SymbolId에도 FK가 없다.
    "SymbolId"           text NOT NULL,
    "Inclusion"          text NOT NULL CHECK ("Inclusion" IN ('topRank','surge','holding')),
    "BaseScore"          double precision,         -- DailyScores의 전일 기준 종합점수
    "FlowPercentileLive" double precision,         -- 장중 잠정 수급 백분위
    "TotalScore"         double precision,         -- 진입·무효 임계 판정의 값
    "LastPrice"          numeric,
    "BuyQuantity"        integer,                  -- 매수 1호가 잔량(점하한가 판정)
    "SellQuantity"       integer,                  -- 매도 1호가 잔량(점상한가 판정)
    "Atr"                numeric,
    "StopWidth"          numeric,                  -- 2.0 × ATR — R 산정 기준
    "IsTradable"         boolean,
    "BlockReason"        text
                         CHECK ("BlockReason" IN ('limitUp','limitDown','halted','vi','overheated')),
    "ScoredDateTime"     timestamptz NOT NULL,
    PRIMARY KEY ("CycleId", "SymbolId")
);

-- 3단계가 낸 제안 주문. 4단계 게이트가 거부·축소할 수 있으므로 확정이 아니다.
-- 점수 미달 무거래는 남기지 않는다(CycleScores로 유추 가능) — costExceedsEdge만 남긴다.
CREATE TABLE IF NOT EXISTS "Decisions" (
    "DecisionId"      text PRIMARY KEY,
    "CycleId"         text NOT NULL REFERENCES "Cycles"("CycleId"),
    "SymbolId"        text NOT NULL,
    "Action"          text NOT NULL
                      CHECK ("Action" IN ('buy','exitAll','raiseStop','noTrade')),
    "Reason"          text NOT NULL
                      CHECK ("Reason" IN ('entryThreshold','thesisInvalid','stopHit','timeExit',
                                          'breakeven','trail','costExceedsEdge')),
    "Score"           double precision,            -- 판정에 쓴 갱신 점수
    "Threshold"       double precision,            -- 그때 적용된 임계값
    "EntryPrice"      numeric,                     -- 진입 기준가(사이클 시점 현재가)
    "StopPrice"       numeric,                     -- 무효화선
    "RiskPerShare"    numeric,                     -- R = 진입가 − 최초 손절가. 청산까지 고정
    "TargetPositions" integer,                     -- 그 시점 목표 보유 종목 수(동일가중의 분모)
    "Quantity"        integer,                     -- 총자본 ÷ TargetPositions ÷ 진입가, 1주 내림
    "RewardRiskRatio" double precision,
    "EstimatedCost"   numeric,                     -- 왕복 거래비용 추정
    "NetEdge"         numeric,                     -- 비용을 뺀 기대 엣지. 음수면 무거래
    "Regime"          text,                        -- MarketIndices에서 복사한 라벨
    "DecidedDateTime" timestamptz NOT NULL
);

-- 4단계 게이트 판정. 여러 규칙이 동시에 걸려도 가장 먼저 걸린 하나만 사유로 남긴다(05-risk 5.2).
CREATE TABLE IF NOT EXISTS "RiskChecks" (
    "CheckId"         text PRIMARY KEY,
    "CycleId"         text NOT NULL REFERENCES "Cycles"("CycleId"),
    "DecisionId"      text REFERENCES "Decisions"("DecisionId"),  -- 사이클 단위 검사는 NULL
    "CheckOrder"      integer NOT NULL,            -- 5.2 검사 순서(1~7) = 심각도
    -- cashFlow: 보유는 맞는데 현금만 어긋나 외부 흐름으로 기록한 경우(매매는 계속 진행)
    "CheckName"       text NOT NULL
                      CHECK ("CheckName" IN ('balanceSync','cashFlow','marketHalt','dataFreshness',
                                             'circuitBreaker','schema','hardLimit','symbolState')),
    -- flowDetected: 차단이 아니라 "기록했고 그대로 진행했다"는 뜻
    "Result"          text NOT NULL
                      CHECK ("Result" IN ('pass','reject','reduce','skipCycle','safeStop',
                                          'flowDetected')),
    "Reason"          text,
    "LimitValue"      double precision,            -- 한도와 실측을 나란히 두면 초과폭 집계가 된다
    "ActualValue"     double precision,
    "CheckedDateTime" timestamptz NOT NULL
);

-- 이미 만들어진 DB용 멱등 이행 — CREATE TABLE IF NOT EXISTS는 기존 표의 CHECK를
-- 갱신하지 않아서, 허용값을 늘릴 때는 제약을 직접 갈아끼워야 한다.
-- DROP IF EXISTS + ADD 쌍이라 여러 번 돌아도 안전하다.
ALTER TABLE "RiskChecks" DROP CONSTRAINT IF EXISTS "RiskChecks_CheckName_check";
ALTER TABLE "RiskChecks" ADD  CONSTRAINT "RiskChecks_CheckName_check"
    CHECK ("CheckName" IN ('balanceSync','cashFlow','marketHalt','dataFreshness',
                           'circuitBreaker','schema','hardLimit','symbolState'));
ALTER TABLE "RiskChecks" DROP CONSTRAINT IF EXISTS "RiskChecks_Result_check";
ALTER TABLE "RiskChecks" ADD  CONSTRAINT "RiskChecks_Result_check"
    CHECK ("Result" IN ('pass','reject','reduce','skipCycle','safeStop','flowDetected'));


-- ════════════════════════════════════════════════════════════
-- 7.3 집행
-- ════════════════════════════════════════════════════════════

-- 외부 현금흐름 1건. 주식은 일치하는데 현금만 어긋난 잔차 = 매매로 설명 불가한 돈.
-- 매매는 주식과 현금을 항상 같이 움직이므로, 주식 대조를 통과했는데 예수금만 틀렸다면
-- 그건 내가 이체했거나 배당이 들어온 것이다(05-risk 5.2 검사 1-b).
-- "Kind"는 단순 라벨이 아니라 회계적으로 의미가 있다: deposit/withdrawal은 TWR에서
-- 제거할 외부 흐름이고, dividend/taxRefund/interest는 수익이라 제거하면 안 된다(09-eval).
CREATE TABLE IF NOT EXISTS "CashFlows" (
    "FlowId"            text PRIMARY KEY,
    "DetectedCycleId"   text REFERENCES "Cycles"("CycleId"),
    "TradeDate"         date NOT NULL,
    "Kind"              text NOT NULL,      -- deposit/withdrawal/dividend/taxRefund/interest/fee/unknown
    "Amount"            numeric NOT NULL,   -- 부호 있음: 유입 +, 유출 −
    "Status"            text NOT NULL,      -- unconfirmed/confirmed/reclassified
    "Source"            text NOT NULL,      -- residual/signature/broker/manual
    "ExpectedCash"      numeric NOT NULL,   -- 감지 시점 기대 예수금 (사후 감사 근거)
    "ActualCash"        numeric NOT NULL,   -- 감지 시점 실제 예수금
    "Note"              text,
    "DetectedDateTime"  timestamptz NOT NULL,
    "ConfirmedDateTime" timestamptz,
    "ConfirmedBy"       text,
    "Mode"              text NOT NULL
);

-- KIS에 보낸 주문. 기본키가 "ClientOrderId"인 것이 중복 주문 방지의 핵심 —
-- 같은 의도면 재시작 후에도 같은 값이 나와 두 번째 삽입이 거부된다.
CREATE TABLE IF NOT EXISTS "Orders" (
    "ClientOrderId"    text PRIMARY KEY,           -- {CycleId}-{SymbolId}-{Side}-{Seq}
    "CycleId"          text REFERENCES "Cycles"("CycleId"),      -- 상주 스톱 자동 체결은 NULL
    "DecisionId"       text REFERENCES "Decisions"("DecisionId"),
    "KisOrderNo"       text,                       -- 정정·취소에 필요
    "SymbolId"         text NOT NULL,
    "Side"             text NOT NULL CHECK ("Side" IN ('buy','sell')),
    "Purpose"          text NOT NULL
                       CHECK ("Purpose" IN ('entry','stop','stopAmend','exit')),
    "OrderType"        text NOT NULL,              -- 00 지정가 · 11 IOC · 22 스톱지정가
    "OrderQuantity"    integer NOT NULL,
    "OrderPrice"       numeric,
    "TriggerPrice"     numeric,                    -- stop·stopAmend만
    "FilledQuantity"   integer NOT NULL DEFAULT 0,
    "AverageFillPrice" numeric,
    "Fee"              numeric,
    "Tax"              numeric,                    -- 거래세(매도만)
    "SlippageEstimate" numeric,
    "Status"           text NOT NULL
                       CHECK ("Status" IN ('submitted','partial','filled','cancelled','rejected')),
    "OrderedDateTime"  timestamptz NOT NULL,
    "FilledDateTime"   timestamptz,
    "Mode"             text NOT NULL               -- "CycleId"가 NULL일 수 있어 따로 둔다
);

-- 보유 상태. 17개 표 중 유일하게 덮어쓰는 표(변경 이력은 Orders로 되짚는다).
-- KIS 실잔고와 대조하는 우리 측 기록이자 진입 결정↔청산 결과를 잇는 다리.
CREATE TABLE IF NOT EXISTS "Positions" (
    "PositionId"        text PRIMARY KEY,
    "SymbolId"          text NOT NULL,
    "Market"            text,                      -- 청산 비용 산정 기준
    "Quantity"          integer NOT NULL,
    "AveragePrice"      numeric NOT NULL,
    "EntryDecisionId"   text REFERENCES "Decisions"("DecisionId"),
    "EntryDate"         date,                      -- 보유일수·시간 기반 청산 기준
    "InitialStopPrice"  numeric,                   -- R 고정 기준. 청산까지 불변
    "CurrentStopPrice"  numeric,                   -- 트레일링·본전 상향으로 변동
    "RiskPerShare"      numeric,                   -- R = AveragePrice − InitialStopPrice
    "IsBreakevenDone"   boolean NOT NULL DEFAULT false,
    "ActiveStopOrderId" text REFERENCES "Orders"("ClientOrderId"),  -- 비면 손절 없이 방치된 포지션
    "Status"            text NOT NULL CHECK ("Status" IN ('open','closed','frozen')),
    "FrozenDateTime"    timestamptz,
    "FrozenPrice"       numeric,                   -- 정지 직전 가격(자본곡선 왜곡 방지)
    "FrozenReason"      text,
    "OpenedDateTime"    timestamptz NOT NULL,
    "UpdatedDateTime"   timestamptz
);

-- 청산 실현손익. 진입 시 점수·레짐을 함께 박아두므로 보정통계 전용 표가 필요 없다.
CREATE TABLE IF NOT EXISTS "Outcomes" (
    "OutcomeId"        text PRIMARY KEY,
    "PositionId"       text REFERENCES "Positions"("PositionId"),
    "EntryDecisionId"  text REFERENCES "Decisions"("DecisionId"),
    "ExitDecisionId"   text REFERENCES "Decisions"("DecisionId"),  -- 상주 스톱 자동 체결은 NULL
    "SymbolId"         text NOT NULL,
    "EntryPrice"       numeric,
    "ExitPrice"        numeric,
    "Quantity"         integer,                    -- 이번 청산 수량(부분 청산이면 그 일부)
    "EntryDate"        date,
    "ExitDate"         date,
    "HoldingDays"      integer,
    "GrossProfitLoss"  numeric,
    "Fee"              numeric,                    -- 수수료(매수·매도 합)
    "Tax"              numeric,
    "NetProfitLoss"    numeric,                    -- 성과 집계는 이 값만 쓴다
    "ReturnPercent"    double precision,
    "RMultiple"        double precision,           -- 손익 ÷ (R × Quantity)
    "ExitKind"         text CHECK ("ExitKind" IN ('partial','full')),   -- 성과 집계 축
    "ExitReason"       text
                       CHECK ("ExitReason" IN ('breakeven','stopHit','timeExit','thesisInvalid','trail')),
    "EntryScore"       double precision,           -- 학습의 원천 ↓
    "EntryScoreBucket" integer,                    -- 보정통계의 집계 축
    "EntryRegime"      text,
    "ClosedDateTime"   timestamptz NOT NULL,
    "Mode"             text NOT NULL
);

-- ════════════════════════════════════════════════════════════
-- 7.4 감사
-- ════════════════════════════════════════════════════════════

-- 매매 전체 정지의 발생·해제. "ReleasedDateTime"이 비어 있으면 지금 정지 중이라는 뜻이고,
-- 사이클 시작 시 이 상태를 조회해 신규 주문을 차단한다(보유 청산은 계속 돈다).
CREATE TABLE IF NOT EXISTS "SafeStopEvents" (
    "EventId"          text PRIMARY KEY,
    "CycleId"          text REFERENCES "Cycles"("CycleId"),
    "OccurredDateTime" timestamptz NOT NULL,
    "Cause"            text NOT NULL,
    "Trigger"          text NOT NULL CHECK ("Trigger" IN ('auto','manual')),
    "ReleasedDateTime" timestamptz,
    "ReleasedBy"       text,                       -- 잔고 불일치·데이터 오류는 사람 개입 필수
    "ReleaseReason"    text
);

-- ════════════════════════════════════════════════════════════
-- 대시보드 계정 권한 — 읽기 전용을 DB가 강제한다(07-model 공통 규칙)
-- ════════════════════════════════════════════════════════════
-- 역할과 계정은 DBA가 한 번 만들고(리포에 비밀번호를 두지 않는다), 표가 늘어날 때마다
-- 아래 GRANT를 다시 돌린다. 역할이 없으면 조용히 건너뛴다(개발용 단일 계정 환경).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'alphaloop_dashboard') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA public TO alphaloop_dashboard';
        EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO alphaloop_dashboard';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
                'GRANT SELECT ON TABLES TO alphaloop_dashboard';
    END IF;
END
$$;
