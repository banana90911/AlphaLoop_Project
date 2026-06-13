"""설정의 단일 로딩 진입점 (09-tech 9.9).

두 갈래로 분리한다:
- **시크릿**: `.env`/환경변수 → `Settings` (리포에 값 없음, 11-2.5)
- **운영 파라미터**: `config/*.toml` → `load_params()` (날짜별·모델별, 그 시점 값으로 과거 재현)

모드(모의/실전/백테스트) 분기는 여기와 `broker/kis_client.py`에서만 한다(03-arch 3.3).
"""
from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).parent
REPO_ROOT = CONFIG_DIR.parent


class Settings(BaseSettings):
    """시크릿 — `.env`/환경변수에서만 로드. 기본값은 빈 문자열(미설정 허용, 사용처에서 검증)."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", extra="ignore", case_sensitive=False
    )

    # ── KIS (실전·모의) ──
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""
    kis_paper_app_key: str = ""
    kis_paper_app_secret: str = ""
    kis_paper_account_no: str = ""
    # ── LLM ──
    anthropic_api_key: str = ""
    # ── 뉴스·공시·매크로 ──
    naver_client_id: str = ""
    naver_client_secret: str = ""
    dart_api_key: str = ""
    fred_api_key: str = ""
    # ── 운영 ──
    healthcheck_url: str = ""
    discord_webhook_url: str = ""
    # ── 모드 (모의/실전 전환은 이 플래그 하나로, 03-arch 3.3 / 11-2.9) ──
    trading_mode: str = "paper"  # "paper" | "real"
    # ── 경로(호스트 비의존, 03-arch 3.3) ──
    db_path: str = "journal.sqlite"


@lru_cache
def get_settings() -> Settings:
    """시크릿 싱글톤."""
    return Settings()


@lru_cache
def load_params(name: str) -> dict:
    """운영 파라미터 toml 로드.

    name ∈ {tax_rates, llm_pricing, rate_limits, models, risk_params}.
    값이 자주 바뀌고 *그 시점 값으로 과거를 재현*해야 하므로 코드·시크릿과 수명을 분리한다.
    """
    path = CONFIG_DIR / f"{name}.toml"
    with path.open("rb") as f:
        return tomllib.load(f)
