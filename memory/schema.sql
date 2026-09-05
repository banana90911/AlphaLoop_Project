-- AlphaLoop PostgreSQL 스키마 — docs/07-data-model.md 표 카탈로그의 구현(단일 소스).
--
-- 공통 규칙(07-model):
--   · 식별자·라벨은 text. 시각은 timestamptz(UTC 저장), 거래일은 date(KST 기준)
--   · 금액·가격·손익은 numeric — 반올림 오차가 자본곡선에 누적되면 안 되기 때문
--     비율·점수·지표는 double precision, 수량·건수는 integer, 참/거짓은 boolean
--   · 컬럼·표 이름은 전부 소문자 스네이크케이스다 — 따옴표 없이 그대로 SQL을 쳐도
--     PostgreSQL의 기본 소문자 접기와 항상 일치한다
--   · 백테스트 결과는 DB에 넣지 않는다(산출물은 파일 — 09-eval). 모의·실전은 mode 열로 구분
--   · 조회 속도용 인덱스는 걸지 않는다 — 실제로 느려진 뒤 추가. 유일성은 전부 기본키가 담당
--   · 접속 계정은 둘(매매 코어=읽기·쓰기 / 대시보드=SELECT만). 권한 부여는 파일 끝 참고
--
-- 표 18개: 시장 데이터 7 · 판단 6 · 집행 4 · 감사 1 (07-model 표 카탈로그와 1:1)

-- ════════════════════════════════════════════════════════════
-- 7.1 시장 데이터 — 장 시작 전 일일 배치가 적재(전 종목 대상)
-- ════════════════════════════════════════════════════════════

-- 종목 명부. KIS 종목마스터 zip을 받아 통째로 덮어쓴다.
CREATE TABLE IF NOT EXISTS symbols (
    symbol_id           text PRIMARY KEY,          -- 종목코드(6자리)
    name               text NOT NULL,
    market             text NOT NULL CHECK (market IN ('KOSPI','KOSDAQ')),
    security_type       text NOT NULL
                         CHECK (security_type IN ('common','preferred','spac','reit','etf','etn')),
    listed_date         date,                      -- 상장 경과일 제외 필터
    delisted_date       date,                      -- NULL이면 상장 중
    dart_corp_code       text,                      -- DART 회사코드(기업행위 조회)
    last_update_date_time timestamptz NOT NULL
);

-- 날짜별 매매 제약 지정 상태. 같은 마스터 파일에서 뽑되 딱지가 붙은 종목만 적재하고,
-- 표에 없으면 정상으로 본다. 날짜별로 쌓는 이유는 백테스트의 룩어헤드 차단(07-model 7.1).
CREATE TABLE IF NOT EXISTS symbol_states (
    symbol_id          text NOT NULL REFERENCES symbols(symbol_id),
    trade_date         date NOT NULL,
    is_halted          boolean NOT NULL DEFAULT false,
    is_admin           boolean NOT NULL DEFAULT false,
    is_warning         boolean NOT NULL DEFAULT false,
    is_overheated      boolean NOT NULL DEFAULT false,
    collected_date_time timestamptz NOT NULL,
    PRIMARY KEY (symbol_id, trade_date)
);

-- 전 종목 일봉. 매일 어제 확정분 한 줄씩 추가(종목당 1호출).
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol_id         text NOT NULL REFERENCES symbols(symbol_id),
    trade_date        date NOT NULL,
    open             numeric,
    high             numeric,                     -- ATR 계산
    low              numeric,                     -- ATR 계산
    close            numeric NOT NULL,            -- 점수 4개 항목의 입력
    volume           bigint,                      -- 주식수는 integer 상한을 넘길 수 있다
    value            numeric,                     -- 거래대금
    adjustment_factor double precision NOT NULL DEFAULT 1.0,  -- 누적 보정 배수(원본 복원용)
    is_adjusted       boolean NOT NULL DEFAULT false,
    PRIMARY KEY (symbol_id, trade_date)
);

-- 외국인·기관 순매수(금액). KIS inquire-investor(최근 30거래일)로 어제분 적재.
CREATE TABLE IF NOT EXISTS daily_flows (
    symbol_id          text NOT NULL REFERENCES symbols(symbol_id),
    trade_date         date NOT NULL,
    foreign_net        numeric,
    institution_net    numeric,
    is_final           boolean NOT NULL DEFAULT false,   -- 잠정치는 false
    collected_date_time timestamptz NOT NULL,
    PRIMARY KEY (symbol_id, trade_date)
);

-- 권리락 등 기업행위. DART 정형 API(fricDecsn·crDecsn·piicDecsn)에서 기준일·배수를 받는다.
-- 배당락은 정형 API가 없어 현재 미수집(04-data 4.1).
CREATE TABLE IF NOT EXISTS corporate_actions (
    action_id          text PRIMARY KEY,
    symbol_id          text NOT NULL REFERENCES symbols(symbol_id),
    ex_date            date NOT NULL,             -- 신주배정기준일·감자기준일
    action_type        text NOT NULL
                        CHECK (action_type IN ('bonus','rights','reduction','split','merger','dividend')),
    price_factor       double precision,          -- 손절선 조정 비율
    detail            text,                      -- 배정비율·감자비율 원자료
    source            text NOT NULL,
    collected_date_time timestamptz NOT NULL
);

-- 코스피·코스닥 지수. 벤치마크 비교(09-eval 게이트)의 유일한 근거이자 레짐 라벨의 원천.
-- 레짐은 결정 입력으로 쓰지 않고 사후 분류 축으로만 쓴다(04-data).
CREATE TABLE IF NOT EXISTS market_indices (
    index_code         text NOT NULL CHECK (index_code IN ('KOSPI','KOSDAQ')),
    trade_date         date NOT NULL,
    close             numeric NOT NULL,
    sma_200            numeric,                   -- 200일 이동평균
    regime            text CHECK (regime IN ('uptrend','downtrend')),
    collected_date_time timestamptz NOT NULL,
    PRIMARY KEY (index_code, trade_date)
);

-- 배치 실행 1회의 결과. 데이터 신선도 검사의 판정 근거이자 재실행 시 이어받기 기준.
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id            text PRIMARY KEY,
    target_table      text NOT NULL,
    source           text NOT NULL,
    range_start_date   date,
    range_end_date     date,
    status           text NOT NULL CHECK (status IN ('ok','partial','failed')),
    target_count      integer,
    success_count     integer,                    -- partial 판정과 이어받기 기준
    rows_written      integer,
    error_message     text,
    started_date_time  timestamptz NOT NULL,
    finished_date_time timestamptz               -- 신선도 판정의 기준
);

-- ════════════════════════════════════════════════════════════
-- 7.2 판단 — 사이클이 계산한 것
-- ════════════════════════════════════════════════════════════

-- 사이클 1회의 실행 기록. Status가 중복 주문 방지의 근거 —
-- ordering에서 죽었으면 KIS 주문 조회로 실제 송출 여부를 먼저 확인한다.
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id          text PRIMARY KEY,           -- 시각 기반 발급. 모든 산출물의 부모 키
    trade_date        date NOT NULL,
    status           text NOT NULL
                       CHECK (status IN ('intent','scoring','deciding','ordering',
                                           'recorded','failed','skipped')),
    skip_reason       text,
    failed_step       integer,
    mode             text NOT NULL,
    started_date_time  timestamptz NOT NULL,
    finished_date_time timestamptz
);

-- 사이클 시점의 계좌 총액. 사이징의 분모이며, 시계열이라야 낙폭을 잴 수 있어 쌓는다.
-- base_asset은 **직전 거래일 마지막 스냅샷의 total_asset**이다(05-risk 5.2 / 09-eval).
-- 예전 이름 "DayStartAsset"은 "당일 첫 사이클의 총자본"으로 읽혀서, 정기 사이클이
-- 하루 한 번인 이 시스템에서는 손익률이 항상 0%가 되는 정의였다 — 그래서 개명했다.
CREATE TABLE IF NOT EXISTS account_snapshots (
    snapshot_id        text PRIMARY KEY,
    cycle_id           text NOT NULL REFERENCES cycles(cycle_id),
    trade_date         date NOT NULL,
    amount            numeric NOT NULL,          -- 예수금
    position_value     numeric NOT NULL,          -- 보유 평가금액(거래정지 종목은 동결가)
    total_asset        numeric NOT NULL,
    base_asset         numeric,                   -- 직전 거래일 마지막 TotalAsset
    net_flow_since_base  numeric NOT NULL DEFAULT 0,  -- 기준선 이후 순외부흐름(입금 +, 출금 −)
    adjusted_base_asset numeric,                   -- BaseAsset + NetFlowSinceBase = 손익률 분모
    cumulative_net_flow numeric NOT NULL DEFAULT 0,  -- 개시 이후 누적 순입금
    twr_index          double precision,          -- 시간가중수익률 지수(1.0에서 시작)
    day_return_percent  double precision,          -- TotalAsset / AdjustedBaseAsset − 1
    recorded_date_time  timestamptz NOT NULL
);

ALTER TABLE account_snapshots ADD COLUMN IF NOT EXISTS net_flow_since_base  numeric NOT NULL DEFAULT 0;
ALTER TABLE account_snapshots ADD COLUMN IF NOT EXISTS adjusted_base_asset numeric;
ALTER TABLE account_snapshots ADD COLUMN IF NOT EXISTS cumulative_net_flow numeric NOT NULL DEFAULT 0;
ALTER TABLE account_snapshots ADD COLUMN IF NOT EXISTS twr_index          double precision;

-- 하루 1회 산출하는 전 종목 점수. 원시값과 백분위를 함께 저장해야
-- 나중에 가중치를 바꿔 재계산할 수 있다(백분위는 그날 통과 집합에 의존).
CREATE TABLE IF NOT EXISTS daily_scores (
    trade_date               date NOT NULL,
    symbol_id                text NOT NULL REFERENCES symbols(symbol_id),
    passed_filter            boolean NOT NULL,    -- 백분위 모집단 기준
    filter_reason            text,
    momentum                double precision,    -- 12-1 모멘텀 원시값
    flow_net_20_day            numeric,             -- 외국인·기관 20일 누적 순매수(금액)
    value_ratio              double precision,    -- 거래대금 5일/60일
    volatility              double precision,    -- 60일 실현변동성
    momentum_percentile      double precision,    -- 가중치 0.35
    flow_percentile          double precision,    -- 0.25
    value_percentile         double precision,    -- 0.15
    low_volatility_percentile double precision,    -- 0.15
    total_score              double precision,    -- 비중 합 0.90을 1.0으로 재정규화
    rank                    integer,             -- 1단계 상위 N 컷의 기준
    computed_date_time        timestamptz NOT NULL,
    PRIMARY KEY (trade_date, symbol_id)
);

-- 워치리스트 종목의 사이클 시점 값. 이 표가 곧 워치리스트다(별도 표 없음).
CREATE TABLE IF NOT EXISTS cycle_scores (
    cycle_id            text NOT NULL REFERENCES cycles(cycle_id),
    -- symbols FK를 걸지 않는다: 워치리스트에는 보유 종목이 무조건 들어가는데(04-data 4.2),
    -- 상장폐지로 명부에서 빠진 보유가 기록 불가가 되면 청산 판단의 근거가 사라진다.
    -- 같은 이유로 decisions·positions·orders의 symbol_id에도 FK가 없다.
    symbol_id           text NOT NULL,
    inclusion          text NOT NULL CHECK (inclusion IN ('topRank','surge','holding')),
    base_score          double precision,         -- DailyScores의 전일 기준 종합점수
    flow_percentile_live double precision,         -- 장중 잠정 수급 백분위
    total_score         double precision,         -- 진입·무효 임계 판정의 값
    last_price          numeric,
    buy_quantity        integer,                  -- 매수 1호가 잔량(점하한가 판정)
    sell_quantity       integer,                  -- 매도 1호가 잔량(점상한가 판정)
    atr                numeric,
    stop_width          numeric,                  -- 2.0 × ATR — R 산정 기준
    is_tradable         boolean,
    block_reason        text
                         CHECK (block_reason IN ('limitUp','limitDown','halted','vi','overheated')),
    scored_date_time     timestamptz NOT NULL,
    PRIMARY KEY (cycle_id, symbol_id)
);

-- 3단계가 낸 제안 주문. 4단계 게이트가 거부·축소할 수 있으므로 확정이 아니다.
-- 점수 미달 무거래는 남기지 않는다(cycle_scores로 유추 가능) — costExceedsEdge만 남긴다.
CREATE TABLE IF NOT EXISTS decisions (
    decision_id      text PRIMARY KEY,
    cycle_id         text NOT NULL REFERENCES cycles(cycle_id),
    symbol_id        text NOT NULL,
    action          text NOT NULL
                      CHECK (action IN ('buy','exitAll','raiseStop','noTrade')),
    reason          text NOT NULL
                      CHECK (reason IN ('entryThreshold','thesisInvalid','stopHit','timeExit',
                                          'breakeven','trail','costExceedsEdge')),
    score           double precision,            -- 판정에 쓴 갱신 점수
    threshold       double precision,            -- 그때 적용된 임계값
    entry_price      numeric,                     -- 진입 기준가(사이클 시점 현재가)
    stop_price       numeric,                     -- 무효화선
    risk_per_share    numeric,                     -- R = 진입가 − 최초 손절가. 청산까지 고정
    target_positions integer,                     -- 그 시점 목표 보유 종목 수(동일가중의 분모)
    quantity        integer,                     -- 총자본 ÷ TargetPositions ÷ 진입가, 1주 내림
    reward_risk_ratio double precision,
    estimated_cost   numeric,                     -- 왕복 거래비용 추정
    net_edge         numeric,                     -- 비용을 뺀 기대 엣지. 음수면 무거래
    regime          text,                        -- MarketIndices에서 복사한 라벨
    decided_date_time timestamptz NOT NULL
);

-- 4단계 게이트 판정. 여러 규칙이 동시에 걸려도 가장 먼저 걸린 하나만 사유로 남긴다(05-risk 5.2).
CREATE TABLE IF NOT EXISTS risk_checks (
    check_id         text PRIMARY KEY,
    cycle_id         text NOT NULL REFERENCES cycles(cycle_id),
    decision_id      text REFERENCES decisions(decision_id),  -- 사이클 단위 검사는 NULL
    check_order      integer NOT NULL,            -- 5.2 검사 순서(1~7) = 심각도
    -- cashFlow: 보유는 맞는데 현금만 어긋나 외부 흐름으로 기록한 경우(매매는 계속 진행)
    check_name       text NOT NULL
                      CHECK (check_name IN ('balanceSync','cashFlow','marketHalt','dataFreshness',
                                             'circuitBreaker','schema','hardLimit','symbolState')),
    -- flowDetected: 차단이 아니라 "기록했고 그대로 진행했다"는 뜻
    result          text NOT NULL
                      CHECK (result IN ('pass','reject','reduce','skipCycle','safeStop',
                                          'flowDetected')),
    reason          text,
    limit_value      double precision,            -- 한도와 실측을 나란히 두면 초과폭 집계가 된다
    actual_value     double precision,
    checked_date_time timestamptz NOT NULL
);

-- 이미 만들어진 DB용 멱등 이행 — CREATE TABLE IF NOT EXISTS는 기존 표의 CHECK를
-- 갱신하지 않아서, 허용값을 늘릴 때는 제약을 직접 갈아끼워야 한다.
-- DROP IF EXISTS + ADD 쌍이라 여러 번 돌아도 안전하다.
ALTER TABLE risk_checks DROP CONSTRAINT IF EXISTS risk_checks_check_name_check;
ALTER TABLE risk_checks ADD  CONSTRAINT risk_checks_check_name_check
    CHECK (check_name IN ('balanceSync','cashFlow','marketHalt','dataFreshness',
                           'circuitBreaker','schema','hardLimit','symbolState'));
ALTER TABLE risk_checks DROP CONSTRAINT IF EXISTS risk_checks_result_check;
ALTER TABLE risk_checks ADD  CONSTRAINT risk_checks_result_check
    CHECK (result IN ('pass','reject','reduce','skipCycle','safeStop','flowDetected'));


-- ════════════════════════════════════════════════════════════
-- 7.3 집행
-- ════════════════════════════════════════════════════════════

-- 외부 현금흐름 1건. 주식은 일치하는데 현금만 어긋난 잔차 = 매매로 설명 불가한 돈.
-- 매매는 주식과 현금을 항상 같이 움직이므로, 주식 대조를 통과했는데 예수금만 틀렸다면
-- 그건 내가 이체했거나 배당이 들어온 것이다(05-risk 5.2 검사 1-b).
-- kind는 단순 라벨이 아니라 회계적으로 의미가 있다: deposit/withdrawal은 TWR에서
-- 제거할 외부 흐름이고, dividend/taxRefund/interest는 수익이라 제거하면 안 된다(09-eval).
CREATE TABLE IF NOT EXISTS cash_flows (
    flow_id            text PRIMARY KEY,
    detected_cycle_id   text REFERENCES cycles(cycle_id),
    trade_date         date NOT NULL,
    kind              text NOT NULL,      -- deposit/withdrawal/dividend/taxRefund/interest/fee/unknown
    amount            numeric NOT NULL,   -- 부호 있음: 유입 +, 유출 −
    status            text NOT NULL,      -- unconfirmed/confirmed/reclassified
    source            text NOT NULL,      -- residual/signature/broker/manual
    expected_cash      numeric NOT NULL,   -- 감지 시점 기대 예수금 (사후 감사 근거)
    actual_cash        numeric NOT NULL,   -- 감지 시점 실제 예수금
    note              text,
    detected_date_time  timestamptz NOT NULL,
    confirmed_date_time timestamptz,
    confirmed_by       text,
    mode              text NOT NULL
);

-- KIS에 보낸 주문. 기본키가 client_order_id인 것이 중복 주문 방지의 핵심 —
-- 같은 의도면 재시작 후에도 같은 값이 나와 두 번째 삽입이 거부된다.
CREATE TABLE IF NOT EXISTS orders (
    client_order_id    text PRIMARY KEY,           -- {CycleId}-{SymbolId}-{Side}-{Seq}
    cycle_id          text REFERENCES cycles(cycle_id),      -- 상주 스톱 자동 체결은 NULL
    decision_id       text REFERENCES decisions(decision_id),
    kis_order_no       text,                       -- 정정·취소에 필요
    symbol_id         text NOT NULL,
    side             text NOT NULL CHECK (side IN ('buy','sell')),
    purpose          text NOT NULL
                       CHECK (purpose IN ('entry','stop','stopAmend','exit')),
    order_type        text NOT NULL,              -- 00 지정가 · 11 IOC · 22 스톱지정가
    order_quantity    integer NOT NULL,
    order_price       numeric,
    trigger_price     numeric,                    -- stop·stopAmend만
    filled_quantity   integer NOT NULL DEFAULT 0,
    average_fill_price numeric,
    fee              numeric,
    tax              numeric,                    -- 거래세(매도만)
    slippage_estimate numeric,
    status           text NOT NULL
                       CHECK (status IN ('submitted','partial','filled','cancelled','rejected')),
    ordered_date_time  timestamptz NOT NULL,
    filled_date_time   timestamptz,
    mode             text NOT NULL               -- cycle_id가 NULL일 수 있어 따로 둔다
);

-- 보유 상태. 17개 표 중 유일하게 덮어쓰는 표(변경 이력은 Orders로 되짚는다).
-- KIS 실잔고와 대조하는 우리 측 기록이자 진입 결정↔청산 결과를 잇는 다리.
CREATE TABLE IF NOT EXISTS positions (
    position_id        text PRIMARY KEY,
    symbol_id          text NOT NULL,
    market            text,                      -- 청산 비용 산정 기준
    quantity          integer NOT NULL,
    average_price      numeric NOT NULL,
    entry_decision_id   text REFERENCES decisions(decision_id),
    entry_date         date,                      -- 보유일수·시간 기반 청산 기준
    initial_stop_price  numeric,                   -- R 고정 기준. 청산까지 불변
    current_stop_price  numeric,                   -- 트레일링·본전 상향으로 변동
    risk_per_share      numeric,                   -- R = AveragePrice − InitialStopPrice
    is_breakeven_done   boolean NOT NULL DEFAULT false,
    active_stop_order_id text REFERENCES orders(client_order_id),  -- 비면 손절 없이 방치된 포지션
    status            text NOT NULL CHECK (status IN ('open','closed','frozen')),
    frozen_date_time    timestamptz,
    frozen_price       numeric,                   -- 정지 직전 가격(자본곡선 왜곡 방지)
    frozen_reason      text,
    opened_date_time    timestamptz NOT NULL,
    updated_date_time   timestamptz
);

-- 청산 실현손익. 진입 시 점수·레짐을 함께 박아두므로 보정통계 전용 표가 필요 없다.
CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id        text PRIMARY KEY,
    position_id       text REFERENCES positions(position_id),
    entry_decision_id  text REFERENCES decisions(decision_id),
    exit_decision_id   text REFERENCES decisions(decision_id),  -- 상주 스톱 자동 체결은 NULL
    symbol_id         text NOT NULL,
    entry_price       numeric,
    exit_price        numeric,
    quantity         integer,                    -- 이번 청산 수량(부분 청산이면 그 일부)
    entry_date        date,
    exit_date         date,
    holding_days      integer,
    gross_profit_loss  numeric,
    fee              numeric,                    -- 수수료(매수·매도 합)
    tax              numeric,
    net_profit_loss    numeric,                    -- 성과 집계는 이 값만 쓴다
    return_percent    double precision,
    r_multiple        double precision,           -- 손익 ÷ (R × Quantity)
    exit_kind         text CHECK (exit_kind IN ('partial','full')),   -- 성과 집계 축
    exit_reason       text
                       CHECK (exit_reason IN ('breakeven','stopHit','timeExit','thesisInvalid','trail')),
    entry_score       double precision,           -- 학습의 원천 ↓
    entry_score_bucket integer,                    -- 보정통계의 집계 축
    entry_regime      text,
    closed_date_time   timestamptz NOT NULL,
    mode             text NOT NULL
);

-- ════════════════════════════════════════════════════════════
-- 7.4 감사
-- ════════════════════════════════════════════════════════════

-- 매매 전체 정지의 발생·해제. released_date_time이 비어 있으면 지금 정지 중이라는 뜻이고,
-- 사이클 시작 시 이 상태를 조회해 신규 주문을 차단한다(보유 청산은 계속 돈다).
CREATE TABLE IF NOT EXISTS safe_stop_events (
    event_id          text PRIMARY KEY,
    cycle_id          text REFERENCES cycles(cycle_id),
    occurred_date_time timestamptz NOT NULL,
    cause            text NOT NULL,
    trigger          text NOT NULL CHECK (trigger IN ('auto','manual')),
    released_date_time timestamptz,
    released_by       text,                       -- 잔고 불일치·데이터 오류는 사람 개입 필수
    release_reason    text
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
