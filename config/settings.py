"""
description:        설정 단일 로딩 진입점 (시크릿 .env + 운영 파라미터 toml)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

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
    # ── 뉴스·공시·매크로 ──
    naver_client_id: str = ""
    naver_client_secret: str = ""
    dart_api_key: str = ""
    fred_api_key: str = ""
    # ── 운영 ──
    healthcheck_url: str = ""
    # SafeStop 전용 ping 주소. 비우면 정상 주소의 /fail로 대신 보낸다.
    healthcheck_safestop_url: str = ""
    discord_webhook_url: str = ""
    # ── 대시보드 — 비밀번호는 해시만 둔다 ──
    dashboard_password_hash: str = ""
    dashboard_token_secret: str = ""
    # ── 모드 (모의/실전 전환은 이 플래그 하나로) ──
    trading_mode: str = "paper"  # "paper" | "real"
    # ── DB 접속 — 코어용(읽기·쓰기)과 대시보드용(SELECT만) 계정을 분리 ──
    db_dsn: str = "postgresql:///journal"
    db_dsn_readonly: str = ""
    # ── 외부 현금흐름(입출금) 감지 — 매매 결정을 바꾸지 않으므로 손잡이 7개에 안 든다 ──
    # 관찰 모드: 흡수 임계 미만 잔차까지 전부 기록해 분포를 모은다(임계 확정 전까지 켜둔다).
    cashflow_observation_mode: bool = True
    # 금액 서명(입금 ...777 / 출금 ...555)으로 Kind를 자동 확정. 기본 비활성(10-ops).
    cashflow_signature_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """시크릿 싱글톤."""
    return Settings()


@lru_cache
def load_params(name: str) -> dict:
    """운영 파라미터 toml 로드(캐시됨). name ∈ {tax_rates, rate_limits, risk_params}."""
    path = CONFIG_DIR / f"{name}.toml"
    with path.open("rb") as f:
        return tomllib.load(f)
