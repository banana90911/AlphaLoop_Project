"""
description:        운영 횡단면 패널 (사이클 1단계 입력)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from datetime import date

import pandas as pd

from data.features import SCREEN_COLS, build_features

# 패널에 함께 담는 값 — 제외 필터(close·adv20)와 사이징·손절(atr) 입력
_EXTRA_COLS = ["close", "atr", "adv20", "ma20", "ma60"]


def latest_row(feats: pd.DataFrame, asof: date | None) -> pd.Series | None:
    """asof 이하 거래일 중 close가 유효한 최신 행을 반환한다(없으면 None)."""
    df = feats if asof is None else feats[feats.index <= asof]
    df = df[df["close"].notna()]
    return df.iloc[-1] if not df.empty else None


def build_panel(
    prices: dict[str, pd.DataFrame], *, asof: date | None = None
) -> pd.DataFrame:
    """종목별 시계열을 asof 기준 횡단면 패널(index=code, columns=지표)로 만든다."""
    cols = SCREEN_COLS + _EXTRA_COLS
    rows: dict[str, dict] = {}
    for code, df in prices.items():
        if df is None or df.empty:
            continue
        feats = build_features(df)
        row = latest_row(feats, asof)
        if row is None:
            continue
        rows[code] = {c: row[c] for c in cols if c in feats.columns}
    return pd.DataFrame.from_dict(rows, orient="index")
