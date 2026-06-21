-- ALphaLoop SQLite 스키마 — 06-data-model.md 객체 카탈로그의 구현(단일 소스).
-- 공통 규칙: PK=*_id(TEXT, 발급자 책임) / 시각=TEXT ISO8601 UTC(KST는 표시 단계) /
--           가격·손익·비율=REAL / 수량·토큰·카운트=INTEGER / bool=INTEGER(0,1).
-- source 라벨(backtest/paper/live)은 학습·체결 계열에만(06 공통규칙 ②).
-- 테이블별 schema_version 컬럼·forward/backward 마이그레이션은 11-2.11 도입 시 추가(현재 user_version으로 관리).

PRAGMA user_version = 1;

-- ── 운영(복구·감사·비용) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trigger_events (
    trigger_id      TEXT PRIMARY KEY,
    detected_at     TEXT NOT NULL,
    subtype         TEXT NOT NULL CHECK(subtype IN ('dart','price_move','vi','circuit_breaker')),
    symbol          TEXT,
    detail          TEXT,
    reference_price REAL,
    fired           INTEGER NOT NULL CHECK(fired IN (0,1)),
    skip_reason     TEXT
);

CREATE TABLE IF NOT EXISTS cycles (
    cycle_id         TEXT PRIMARY KEY,
    status           TEXT NOT NULL CHECK(status IN ('intent','ordering','recorded','failed')),
    trigger_type     TEXT NOT NULL CHECK(trigger_type IN ('scheduled','event')),
    trigger_event_id TEXT REFERENCES trigger_events(trigger_id),
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    failed_agents    TEXT
);

CREATE TABLE IF NOT EXISTS safe_stop_events (
    event_id       TEXT PRIMARY KEY,
    occurred_at    TEXT NOT NULL,
    cause          TEXT NOT NULL,
    auto_or_manual TEXT NOT NULL CHECK(auto_or_manual IN ('auto','manual')),
    released_at    TEXT,
    released_by    TEXT,
    release_reason TEXT,
    cycle_id       TEXT REFERENCES cycles(cycle_id)
);

-- ── 의사결정(positions가 FK로 참조하므로 먼저) ──────────────────
CREATE TABLE IF NOT EXISTS decisions (
    decision_id      TEXT PRIMARY KEY,
    cycle_id         TEXT NOT NULL REFERENCES cycles(cycle_id),
    symbol           TEXT,
    action           TEXT NOT NULL CHECK(action IN ('buy','sell','trim','trail','exit','no_trade')),
    side             TEXT CHECK(side IN ('buy','sell')),
    qty_risk_budget  REAL,
    rationale        TEXT,
    entry_thesis     TEXT,          -- JSON: catalyst·invalidation_price·rr_ratio·net_edge_after_cost
    stop_loss        REAL,
    take_profit      REAL,
    exit_plan        TEXT,
    confidence       REAL,
    dissent_addressed TEXT,
    no_trade_reason  TEXT,
    context_snapshot TEXT,          -- Warm 진입 시 NULL로 압축
    regime_tag       TEXT,
    session_label    TEXT CHECK(session_label IN ('morning','afternoon')),
    source           TEXT NOT NULL CHECK(source IN ('backtest','paper','live')),
    decided_at       TEXT NOT NULL
);

-- ── 보유·체결·결과(사실) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS positions (
    position_id        TEXT PRIMARY KEY,
    symbol             TEXT NOT NULL,
    qty                INTEGER NOT NULL,
    avg_price          REAL NOT NULL,
    sector             TEXT,
    market             TEXT,                                 -- KOSPI/KOSDAQ(거래세율·비용 산식). 매핑 부재 시 NULL
    entry_decision_id  TEXT REFERENCES decisions(decision_id),
    initial_stop_price REAL,                                 -- R 고정 기준(진입 시 최초 손절, 불변 — 05-risk §97)
    current_stop_price REAL,                                 -- 트레일링·본전 상향으로 변동
    tp1_done           INTEGER NOT NULL DEFAULT 0,           -- +1.5R 부분익절 1회 완료 여부
    entry_date         TEXT,                                 -- 보유일·비용 산정 기준일(YYYY-MM-DD)
    status             TEXT NOT NULL CHECK(status IN ('open','closed')),
    opened_at          TEXT NOT NULL,
    updated_at         TEXT
);

CREATE TABLE IF NOT EXISTS frozen_positions (
    frozen_id        TEXT PRIMARY KEY,
    position_id      TEXT REFERENCES positions(position_id),
    symbol           TEXT NOT NULL,
    frozen_at        TEXT NOT NULL,
    last_valid_price REAL NOT NULL,
    halt_reason      TEXT NOT NULL,
    released_at      TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id        TEXT PRIMARY KEY,
    cycle_id        TEXT REFERENCES cycles(cycle_id),
    decision_id     TEXT REFERENCES decisions(decision_id),  -- 상주 스톱 자동체결은 NULL
    client_order_id TEXT NOT NULL,                            -- idempotency 키
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK(side IN ('buy','sell')),
    ord_dvsn        TEXT NOT NULL,                            -- 00 지정가·22 스톱지정가 등(API 명세)
    order_qty       INTEGER NOT NULL,
    filled_qty      INTEGER NOT NULL DEFAULT 0,
    order_price     REAL,                                     -- ORD_UNPR
    trigger_price   REAL,                                     -- CNDT_PRIC(스톱)
    fill_price      REAL,
    fee             REAL,
    tax             REAL,
    slippage_est    REAL,
    status          TEXT NOT NULL CHECK(status IN ('submitted','filled','partial','cancelled','rejected')),
    ordered_at      TEXT NOT NULL,
    filled_at       TEXT,
    source          TEXT NOT NULL CHECK(source IN ('backtest','paper','live'))
);

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id        TEXT PRIMARY KEY,
    position_id       TEXT REFERENCES positions(position_id),
    entry_decision_id TEXT REFERENCES decisions(decision_id),
    symbol            TEXT NOT NULL,
    entry_price       REAL,
    exit_price        REAL,
    qty               INTEGER,
    holding_days      INTEGER,
    gross_pnl         REAL,
    net_pnl           REAL,
    return_pct        REAL,
    exit_reason       TEXT,
    closed_at         TEXT NOT NULL,
    source            TEXT NOT NULL CHECK(source IN ('backtest','paper','live'))
);

-- ── LLM 감사 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id               TEXT PRIMARY KEY,
    cycle_id              TEXT REFERENCES cycles(cycle_id),
    agent_role            TEXT NOT NULL,   -- catalyst/decider (+reflection 향후), enum 확장 가능
    model_id              TEXT,
    model_version         TEXT,
    request_payload       TEXT,
    response_payload      TEXT,
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    cache_creation_tokens INTEGER,
    cache_read_tokens     INTEGER,
    cost_usd              REAL,
    cost_krw              REAL,
    latency_ms            INTEGER,
    retry_count           INTEGER DEFAULT 0,
    parse_status          TEXT CHECK(parse_status IN ('ok','repaired','retried','failed')),
    called_at             TEXT NOT NULL
);

-- ── 학습(정성·정량) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_predictions (
    prediction_id            TEXT PRIMARY KEY,
    cycle_id                 TEXT REFERENCES cycles(cycle_id),
    decision_id              TEXT REFERENCES decisions(decision_id),
    symbol                   TEXT NOT NULL,
    agent_role               TEXT NOT NULL,   -- catalyst(뉴스)/decider, enum 확장 가능
    view                     TEXT CHECK(view IN ('bullish','bearish','neutral')),
    confidence               REAL,
    key_signals              TEXT,            -- JSON 배열(출처 라벨 포함)
    key_risks                TEXT,
    rationale                TEXT,            -- Warm 진입 시 50자 축약
    model_version            TEXT,            -- 의도적 복제(모델별 보정 재현)
    correct                  INTEGER CHECK(correct IN (0,1)),
    tentative                INTEGER CHECK(tentative IN (0,1)),
    attribution_score        REAL,
    realized_pnl_attribution REAL,
    source                   TEXT NOT NULL CHECK(source IN ('backtest','paper','live'))
);

CREATE TABLE IF NOT EXISTS lessons (
    lesson_id     TEXT PRIMARY KEY,
    text          TEXT NOT NULL,
    setup_tag     TEXT,
    regime_tag    TEXT,           -- 자체 복제(교훈은 사이클보다 오래 산다)
    sample_n      INTEGER,
    confidence    REAL,
    expiry        TEXT,           -- 기본 24개월
    applied_count INTEGER DEFAULT 0,
    helped_score  REAL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_outcomes (
    shadow_id          TEXT PRIMARY KEY,
    decision_id        TEXT REFERENCES decisions(decision_id),
    symbol             TEXT NOT NULL,
    reject_reason      TEXT,
    entry              REAL,
    stop               REAL,
    target             REAL,
    virtual_exit_price REAL,
    virtual_pnl        REAL,
    size_sim_applied   INTEGER CHECK(size_sim_applied IN (0,1)),
    regime_tag         TEXT,
    created_at         TEXT NOT NULL,
    source             TEXT NOT NULL CHECK(source IN ('backtest','paper','live'))
);

-- ── 집계(장기 보존) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS calibration_cold (   -- Cold(2~5년) raw 삭제 후 월별 집계
    period        TEXT NOT NULL,                 -- 연월
    agent_role    TEXT NOT NULL,
    regime        TEXT,
    correct_count INTEGER NOT NULL,
    n             INTEGER NOT NULL,
    PRIMARY KEY (period, agent_role, regime)
);

CREATE TABLE IF NOT EXISTS shadow_summary (      -- shadow 1년 경과 raw 삭제 후 연도·레짐 집계
    period          TEXT NOT NULL,               -- 연도
    regime          TEXT,
    reject_rate     REAL,
    avg_virtual_pnl REAL,
    PRIMARY KEY (period, regime)
);

-- ── 보정 집계 뷰(저장 아님 — 쿼리 시 집계). 미가중 raw 집계.
--    시간가중·수축(shrunk_rate)·Wilson 신뢰구간은 memory/calibration.py(코드)에서 산출.
CREATE VIEW IF NOT EXISTS calibration AS
SELECT
    ap.agent_role,
    d.regime_tag                      AS regime,
    CAST(ap.confidence * 10 AS INTEGER) AS confidence_bucket,
    SUM(ap.correct)                   AS correct_count,
    COUNT(*)                          AS n
FROM agent_predictions ap
JOIN decisions d ON ap.decision_id = d.decision_id
WHERE ap.correct IS NOT NULL
GROUP BY ap.agent_role, d.regime_tag, confidence_bucket;

-- ── 인덱스(조회 패턴) ───────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_decisions_cycle   ON decisions(cycle_id);
CREATE INDEX IF NOT EXISTS idx_trades_cycle      ON trades(cycle_id);
CREATE INDEX IF NOT EXISTS idx_trades_symbol     ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_outcomes_position ON outcomes(position_id);
CREATE INDEX IF NOT EXISTS idx_predictions_decision ON agent_predictions(decision_id);
CREATE INDEX IF NOT EXISTS idx_positions_status  ON positions(status);
CREATE INDEX IF NOT EXISTS idx_lessons_active    ON lessons(active);
CREATE INDEX IF NOT EXISTS idx_llm_calls_cycle   ON llm_calls(cycle_id);
