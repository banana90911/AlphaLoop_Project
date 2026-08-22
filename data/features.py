"""피처·제외 필터 단일 정의 (04-data 4.2). 운영과 백테스트가 **같은 코드**를 쓴다.

과거에 백테스트가 자체 정의를 들고 있다가 설계·운영과 어긋난 적이 있어(09-eval 9.6.3),
점수에 들어가는 값의 정의는 여기 한 곳에만 둔다. 시세 수집(네트워크)은 다른 레이어의
일이고, 여기는 *이미 메모리에 있는 시계열*만 다룬다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data import indicators as ind

# 04-data 4.2 제외 필터 임계 (문서 고정값 — 튜닝 손잡이가 아니다)
MIN_PRICE = 2_000.0          # 동전주 제외
MIN_ADV20 = 3_000_000_000.0  # 최근 20일 평균 거래대금 30억 미만 제외

# 스크리너가 읽는 패널 컬럼 (04-data 4.2 네 항목). supply5·alignment는 점수에서 빠졌으나
# 지표 자체는 계속 계산한다 — 분석·재검토 때 다시 쓸 수 있고 계산 비용이 없다.
SCREEN_COLS = ["momentum", "supply20", "value", "lowvol"]


def build_features(df: pd.DataFrame, *, supply_windows: tuple[int, int] = (5, 20)) -> pd.DataFrame:
    """종목 OHLCV(+수급) → 04-data 4.2 정의의 피처 시계열. index=date.

    - momentum: 12-1 모멘텀 = close_{t-20} / close_{t-252} − 1
    - lowvol:   60일 실현변동성 (낮을수록 가점)
    - value:    거래대금 증가 = 5일 평균 ÷ 60일 평균
    - supply20: 외국인·기관 순매수의 20일 누적
    - atr:      손절폭·트레일링용 ATR20 (점수 항목 아님)
    - alignment·supply5: 점수에서 뺀 항목(09-eval 9.5). 지표는 계속 산출한다
    """
    close, high, low = df["close"], df["high"], df["low"]
    out = pd.DataFrame(index=df.index)
    for col in ("open", "high", "low", "close"):
        if col in df:
            out[col] = df[col]
    out["momentum"] = ind.momentum(close, 252, skip=20)
    out["atr"] = ind.atr(high, low, close, 20)
    out["lowvol"] = ind.realized_vol(close, 60)
    out["ma20"] = ind.sma(close, 20)
    out["ma60"] = ind.sma(close, 60)
    out["alignment"] = alignment(close, out["ma20"], out["ma60"])
    turnover = close * df["volume"]
    out["adv20"] = turnover.rolling(20).mean()
    out["value"] = turnover.rolling(5).mean() / turnover.rolling(60).mean()
    w_short, w_long = supply_windows
    if "foreign_net" in df or "inst_net" in df:
        flow = (df.get("foreign_net", pd.Series(np.nan, index=df.index)).fillna(0)
                + df.get("inst_net", pd.Series(np.nan, index=df.index)).fillna(0))
        out["supply5"] = flow.rolling(w_short).sum()
        out["supply20"] = flow.rolling(w_long).sum()
    else:
        out["supply5"] = np.nan
        out["supply20"] = np.nan
    return out


def alignment(close: pd.Series, ma20: pd.Series, ma60: pd.Series) -> pd.Series:
    """정배열 점수 0·0.5·1 — 종가>20일선, 20일선>60일선 각각 가점 (04-data 4.2).

    현재가를 그대로 받으므로 사이클 시점 시세로 갱신할 수 있다(4.2 장중 갱신 정책).
    """
    return ((close > ma20).astype(float) + (ma20 > ma60).astype(float)) / 2.0


def eligible(panel: pd.DataFrame) -> pd.Index:
    """04-data 4.2 제외 필터를 통과한 종목 (점수 백분위의 모집단).

    적용: 동전주(2,000원 미만)·20일 평균 거래대금 30억 미만·상장 경과일(모멘텀 워밍업).
    미적용(데이터 부재): 관리종목·거래정지·투자경고/위험·정리매매 — KIS 종목마스터의
    상태값은 *오늘* 스냅샷만 구할 수 있어 과거 재생에 걸 수 없다(09-eval 9.6.7).
    """
    if panel.empty:
        return panel.index[:0]
    close, adv, mom = panel.get("close"), panel.get("adv20"), panel.get("momentum")
    if close is None or adv is None or mom is None:
        return panel.index
    ok = (
        close.notna() & (close >= MIN_PRICE)
        & adv.notna() & (adv >= MIN_ADV20)
        & mom.notna()                       # 12-1 모멘텀 워밍업 = 상장 경과일 대용
    )
    return panel.index[ok.fillna(False)]
