"""읽기 전용 조회 API — FastAPI (08-dashboard 8.1·8.3).

**경계가 이 파일의 존재 이유다.** 대시보드는 읽기 전용이고, 매매 코어는 대시보드의
존재를 모른다. 둘은 DB로만 만난다. 그래서 여기서는 `SELECT` 전용 계정으로 붙고
(`memory.db.connect(read_only=True)`), 매매 코어의 함수를 한 줄도 부르지 않는다.

**계약이 프론트보다 오래 산다.** 화면을 나중에 통째로 갈아엎어도 이 JSON 계약은
그대로다. 그래서 API를 먼저 만들고 화면을 뒤에 붙인다.

모든 조회는 출입증(`dashboard.auth`)을 요구한다. 없거나 만료됐으면 데이터를 한 줄도
주지 않는다.

미구현 — 아래 함수는 전부 뼈대다. 라우팅은 화면 4영역(08-dashboard 8.4)에 대응한다.
"""
from __future__ import annotations


def get_account():
    """① 나의 정보 — 총자본·예수금·평가손익·당일 손익률, 보유 종목.

    평가손익은 `Positions.AveragePrice` vs 마지막 사이클이 본 가격
    (`CycleScores.LastPrice`)로 낸다. 그 사이클 시각도 함께 준다 — 언제 기준의
    값인지 모르면 숫자를 못 믿는다.
    """
    raise NotImplementedError


def get_equity_curve(period: str = "all"):
    """② 수익 그래프 — `Outcomes.NetProfitLoss`를 청산일 순으로 누적.

    성과 판단은 이 선 하나로만 한다(비용 차감 후 실현손익). 벤치마크는
    `MarketIndices`의 코스피·코스닥을 겹쳐 준다.
    """
    raise NotImplementedError


def get_trades(start=None, end=None, side=None):
    """③ 거래 리포트 — `Orders`·`Positions`·`Outcomes`를 시간순으로."""
    raise NotImplementedError


def get_trade_detail(client_order_id: str):
    """③ 펼침 — 진입 근거·게이트 결과·청산 결과를 한 거래 단위로 모아 준다.

    진입 근거는 `Decisions`·`CycleScores`, 게이트는 `RiskChecks`, 청산은 `Outcomes`에서.

    전부 숫자라 행이 작다. 압축·삭제 없이 영구 보존하므로 몇 년 전 거래도 근거가 남는다.
    """
    raise NotImplementedError


def get_alerts():
    """④ 오류·정지 — `SafeStopEvents`·`Cycles`(failed·skipped)·`IngestRuns`(failed·partial).

    무엇을 확인하고 어떻게 푸는지까지 알려준다. **해제 버튼은 두지 않는다** — 읽기
    전용이고, 잔고 불일치·데이터 오류는 사람이 직접 개입해야 한다(05-risk 5.4).
    """
    raise NotImplementedError
