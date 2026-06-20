"""운영 횡단면 패널 (data/panel, 03-arch 3.1 1단계·04-data 0c).

사이클 1단계(후보 선별)의 입력을 만든다. 종목별 시계열(prices)에서 `asof` 거래일 기준
*최신 한 행*만 뽑아 종목×지표 패널을 세운다 — 이게 `screener.screen`이 백분위로
줄세우는 입력이다. 백테스트의 `engine._cross_section`은 모든 종목이 같은 거래일을
가진다고 보지만, 운영 데이터는 거래정지·신규상장으로 종목마다 마지막 거래일이 어긋날 수
있어 *정확 일치 대신 ≤asof 최신 행*을 쓴다.

피처 계산은 `engine.build_features`를 그대로 재사용한다(백테스트와 운영이 같은 정의를
쓰게 — 둘이 갈리면 백테스트로 고른 가중치가 운영에서 어긋난다). 시세 수집(네트워크)은
별 레이어(data.collect·loader)의 일이고, 여기는 *이미 메모리에 있는 시계열*만 다룬다.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from backtest.engine import _SCREEN_COLS, build_features


def latest_row(feats: pd.DataFrame, asof: date | None) -> pd.Series | None:
    """asof(미지정이면 끝) 이하 거래일 중 close가 유효한 *최신 행*. 없으면 None."""
    df = feats if asof is None else feats[feats.index <= asof]
    df = df[df["close"].notna()]
    return df.iloc[-1] if not df.empty else None


def build_panel(
    prices: dict[str, pd.DataFrame],
    *,
    asof: date | None = None,
    supply_window: int = 20,
) -> pd.DataFrame:
    """종목별 시계열 → asof 기준 횡단면 패널. index=code, columns=스크리너 지표.

    워밍업 미완(momentum 등 NaN)이나 asof 이하 데이터가 없는 종목은 빠진다. 결측 *지표*는
    그대로 둬 스크리너가 중립(0.5)으로 처리한다(한 지표 결측이 종목을 통째로 떨구지 않게).
    """
    rows: dict[str, dict] = {}
    for code, df in prices.items():
        if df is None or df.empty:
            continue
        feats = build_features(df, supply_window=supply_window)
        row = latest_row(feats, asof)
        if row is None:
            continue
        rows[code] = {c: row[c] for c in _SCREEN_COLS if c in feats.columns}
    return pd.DataFrame.from_dict(rows, orient="index")
