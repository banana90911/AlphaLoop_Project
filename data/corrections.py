"""수정주가·신선도·vintage 보정 (04-data 4.1·4.3).

받아온 시세를 그대로 믿지 않고 한 겹 검증하는 자리다. 세 가지를 다룬다.

**① 수정주가** — 유상·무상증자, 액면분할, 배당락으로 주가에 인위적인 단차가 생긴다.
KIS 수정주가를 우선 쓰되 그것만 믿지 않는다. 갭 크기로 걸러내는 방식은 큰 권리락은
잡지만 배당락(1~3%)·소규모 권리락(5~30%)은 제한폭 안이라 판별이 불가능하다 —
그래서 `CorporateActions`의 기준일을 함께 본다(04-data 4.1).

**② 신선도** — 외부 데이터는 조용히 실패하거나 늦게 온다. 계좌·시세가 비거나
이상하면 그 종목 매매를 멈춘다. 다만 **지표 하나가 비는 것은 백분위 환산에서
중립(0.5)으로 처리**해, 한 지표 결측으로 종목이 통째로 탈락하지 않게 한다.

**③ vintage 갭** — 백테스트가 본 데이터와 실거래 시점에 실제로 가용했던 데이터의
차이. yfinance 지수는 과거 값이 조용히 정정되므로, 지금 받은 데이터로 과거를 재생하면
그때는 다른 모습이었던 값으로 결정했다고 착각하게 된다(04-data 4.3).

미구현 — 아래 함수는 전부 뼈대다.
"""
from __future__ import annotations

from datetime import date

import pandas as pd


def apply_adjustments(bars: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """기업행위 기준일·배수로 과거 시계열을 수정주가로 되돌린다.

    `DailyBars.AdjustmentFactor`에 누적 보정 배수를 남겨 원본 복원이 가능하게 하고,
    보정을 적용한 행은 `IsAdjusted`로 표시한다(07-model).
    """
    raise NotImplementedError


def adjust_stop_for_action(stop_price: float, price_factor: float) -> float:
    """권리락 당일 보유 종목의 손절선을 같은 비율로 내린다.

    안 내리면 기준가가 떨어진 그 순간 손절이 통째로 발동한다. 조정 뒤에는 KIS에 걸어둔
    스톱 주문도 정정해야 한다(`Orders.Purpose='stopAmend'`).
    """
    raise NotImplementedError


def check_freshness(conn, *, trade_date: date, max_age_hours: float) -> tuple[bool, str]:
    """오늘 배치가 제때 `ok`로 끝났는지 `IngestRuns`로 확인. 반환: (통과 여부, 사유).

    사이클 4단계 게이트(`dataFreshness`)가 이 판정을 쓴다 — 낡은 데이터 위에서
    결정하는 것을 막는 마지막 관문이다(05-risk 5.2).
    """
    raise NotImplementedError
