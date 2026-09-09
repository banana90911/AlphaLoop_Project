# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

한국 주식(코스피·코스닥) 모멘텀+수급 스윙 전략을 전부 코드(통계·규칙)로 자동 집행하는 1인용 개인 프로젝트다. LLM은 초기 구축 단계에서 쓰지 않는다 — 후보 선별·결정·사이징·집행 전 단계가 결정론적 코드다. 설계 정본은 `docs/01`~`docs/10` (README.md의 표에 목차 있음)이며, 코드보다 설계 문서가 먼저 확정된다. 외부 API 명세는 `docs/reference/external-apis.md`.

## 명령어

```bash
# 의존성 (그룹별 optional-dependencies — 필요한 그룹만 설치)
pip install -e ".[dev,data,backtest,dashboard]"

# 테스트 (PostgreSQL 필요 없음 — conftest가 pgserver로 1인용 서버를 즉석에서 띄움)
pytest
pytest tests/test_sizing.py
pytest tests/test_sizing.py::test_함수이름 -v

# 린트 / 타입체크
ruff check .
mypy .

# 대시보드 화면 개발 (실계좌·실DB 전혀 건드리지 않는 데모 경로)
python -m dashboard.devserve          # API: :8787, pgserver 임시 DB + 가짜 데이터 시딩
cd dashboard/web && npm run dev       # 화면: :5173
cd dashboard/web && npm run build     # tsc -b && vite build

# 운영 진입점 (실행에는 KIS 키 등 .env 필요)
python run_daily_ingest.py            # 장 시작 전 배치 — 전종목 데이터·점수 준비
python run_cycle.py                   # 매매 사이클 1회 (--live 없으면 집행 계획까지만)
python run_watch.py                   # 장중 보유 감시 (손절 스톱 무결성만)
python run_gate.py                    # Go/No-Go 게이트 — 동결 기본값 연속 백테스트 1회
python run_walkforward.py             # 워크포워드 OOS 검증 (그리드는 최소한만)
```

## 아키텍처

### 한 사이클의 흐름 (6단계, `pipeline/cycle.py`가 조립)

1. **후보 선별** (`pipeline/screening.py`, `data/screener.py`) — 전 종목에서 매매 부적합 제외, 점수 상위 40 + 당일 급등 + 보유 전부를 워치리스트로
2. **데이터 수집** (`data/market_data.py`, `data/collect.py`) — 워치리스트 현재가·호가·잠정 수급 + 글로벌·매크로
3. **결정** (`pipeline/decision.py`) — 종합점수 갱신 → 진입/무효 임계 판정 → `risk/sizing.py`가 수량 환산
4. **리스크 검증** (`risk/risk_engine.py`) — 잔고 정합성 → 시장 상태 → 데이터 신선도 → 서킷브레이커 → 하드룰 → 종목 상태 순서 고정
5. **주문 실행** (`exec/orders.py`, `broker/kis_client.py`) — KIS 송출 + 체결 즉시 손절 스톱 등록 (`exec/exits.py`가 스톱 무결성 감시)
6. **기록** (`memory/journal.py`, `memory/db.py`) — 점수·결정 근거·게이트 결과·체결 전부 DB에 적재

과거 성과는 별도 조회 단계가 아니라 레짐별 승률·손익비 집계로 3단계(사이징)에 직접 들어간다.

### 백테스트 vs 실거래 — 같은 코드 경로

`backtest/spec_engine.py`가 정본 백테스트 엔진이다(`backtest/loader.py`가 데이터 로드, `backtest/walkforward.py`가 OOS 분할). **결정 로직은 백테스트와 실거래가 동일 코드를 써야 한다** — 이것이 핵심 설계 원칙 1번이다. `backtest/engine.py`류의 별도 구현은 두지 않는다(과거 구엔진은 설계 위반으로 폐기됨). `eval/gate.py` + `eval/metrics.py`가 벤치마크 4종 대비 성과·PBO·Deflated Sharpe로 Go/No-Go를 가른다.

### 설정 — 시크릿과 운영 파라미터를 분리

- `config/settings.py`의 `get_settings()` — `.env`에서만 로드되는 시크릿(API 키, DB DSN, 대시보드 비밀번호 해시 등), `@lru_cache` 싱글톤.
- `config/settings.py`의 `load_params(name)` — `config/{tax_rates,rate_limits,risk_params}.toml`의 운영 파라미터(전략 손잡이), `@lru_cache`로 캐시된 **dict를 그대로 반환**한다. 조정 실험(그리드서치, run_gate.py의 KNOBS 등)에서는 반드시 `copy.deepcopy` 후 수정할 것 — 안 그러면 전역 캐시가 오염된다.
- 모의/실전 전환은 `trading_mode` 설정값 하나로만 하고 코드에 `if 모드` 분기를 두지 않는 것이 불변식이다(예외는 `config/`, `broker/kis_client.py`, 테스트, 문서뿐). CI로 `if .*(live|paper|backtest).*:` 패턴을 걸러내는 게 설계 목표이나 아직 워크플로우로 구현되진 않았다(`docs/10-operations.md` 10.8 참조) — 이 규칙은 리뷰 시 사람이 직접 챙겨야 한다.

### 저장소 — PostgreSQL 단일 진실원

- 매매 코어·대시보드·모든 배치가 같은 PostgreSQL을 공유점으로 쓴다. 스키마는 `memory/schema.sql`(19개 표, `docs/07-data-model.md`가 정본), 마이그레이션은 아직 Alembic 미도입 상태로 `memory/migrations/README.md`에 절차만 있다.
- **DB 연결은 반드시 `memory/db.py`의 `connect()`/`init_db()`를 거친다.** `psycopg.connect`/`sqlite3.connect` 직접 호출은 `ruff`의 `banned-api` 룰로 차단된다(`pyproject.toml`).
- 쓰기·조회 계정이 분리되어 있다(`db_dsn` vs `db_dsn_readonly`) — 대시보드 API는 조회 전용 계정만 쓴다.
- 테스트는 실 PostgreSQL이 없어도 된다: `tests/conftest.py`의 `conn` 픽스처가 `pgserver`로 1인용 서버를 띄우고 테스트마다 임시 스키마를 만들었다 지운다. DSN 우선순위는 `ALPHALOOP_TEST_DSN` 환경변수 → pgserver → 설정값.

### 시간 처리

`core/timeutils.py`(KST 변환), `core/trading_days.py`(거래일 판정, `exchange_calendars` 기반)를 거쳐야 한다. 인자 없는 `datetime.now()`(naive) 직접 호출은 설계상 금지 대상이다(`docs/10-operations.md` 10.8) — 항상 타임존 인지 시각을 쓸 것.

### 대시보드

`dashboard/api.py`(FastAPI, 조회 전용) + `dashboard/auth.py`(비밀번호 로그인, JWT류 토큰) + `dashboard/web`(React 19 + Vite + Tailwind v4, `echarts`로 시각화). 배포는 API/DB는 NCP 서버, 화면은 Vercel, 둘을 Tailscale Funnel로 연결한다. 화면 개발 중엔 `dashboard/devserve.py`(실DB/실계좌 절대 미접촉, pgserver + 가짜 데이터)로 로컬 검증한다.

## 코드 스타일 규약

- 모든 모듈 최상단에 `description / author / created date / last modified date / remarks` 형식의 한국어 docstring 헤더가 있다(`config/settings.py`, `run_cycle.py` 등 참조). 새 파일을 만들 때 이 형식을 따른다.
- 주석과 커밋 메시지는 한국어.
- `mypy strict = true` — 타입 힌트를 생략하지 않는다.
