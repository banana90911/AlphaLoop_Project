"""
description:        리스크 엔진 — 파산 방지 결정론 바닥 (하드 한도·서킷브레이커)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from dataclasses import dataclass, field


@dataclass
class Position:
    """보유 종목 스냅샷"""
    code: str
    qty: int
    last_price: float
    market: str = "KOSPI"

    @property
    def value(self) -> float:
        return self.qty * self.last_price


@dataclass
class Account:
    """계좌 스냅샷. start_capital=서킷브레이커 기준선(직전 거래일 마지막 총자산)."""
    start_capital: float
    cash: float
    positions: list[Position] = field(default_factory=list)
    net_external_flow: float = 0.0   # 기준선 이후 순외부흐름(입금 +, 출금 −)

    @property
    def equity(self) -> float:
        return self.cash + sum(p.value for p in self.positions)

    @property
    def baseline(self) -> float:
        """서킷브레이커 기준선 — 외부 현금흐름만큼 평행이동(05-risk 5.2).

        입금은 기준선을 올리고 출금은 내린다. 안 옮기면 출금이 가짜 −%를 만들어
        브레이커를 헛발동시키고, 입금은 반대로 진짜 손실을 가린다.
        """
        return self.start_capital + self.net_external_flow

    def position_value(self, code: str) -> float:
        return sum(p.value for p in self.positions if p.code == code)


@dataclass
class Verdict:
    """판정 결과. allowed=False면 reason에 단일 사유, check에 걸린 검사 이름."""
    allowed: bool
    reason: str = ""
    check: str = ""              # risk_checks.check_name (없으면 빈 문자열)

    def __bool__(self) -> bool:
        return self.allowed


def daily_loss_pct(acc: Account) -> float:
    """당일 손익률 = 평가액 / 조정 기준선 − 1(음수=손실).

    외부 현금흐름은 기준선을 평행이동시킨다 — 이체는 손익이 아니기 때문이다.
    `net_external_flow`가 0이면 예전 식과 완전히 같다(백테스트가 이 경우다).
    """
    base = acc.baseline
    if base <= 0:
        return 0.0
    return acc.equity / base - 1.0


def breakers_tripped(acc: Account, params: dict) -> set[str]:
    """발동된 서킷브레이커 집합을 반환한다(비면 정상)."""
    lim = params["limits"]
    tripped: set[str] = set()
    if daily_loss_pct(acc) <= -lim["daily_loss_pct"]:
        tripped.add("daily_loss")
    return tripped


def check_new_buy(acc: Account, code: str, add_value: float, params: dict) -> Verdict:
    """신규/추가 매수 금액이 총노출 하드 한도를 넘는지 판정한다."""
    lim = params["limits"]
    eq = acc.equity
    if eq <= 0:
        return Verdict(False, "자본 0 이하", "hardLimit")
    if add_value <= 0:
        return Verdict(False, "매수 금액 0 이하", "hardLimit")
    eps = 1e-6
    held = sum(p.value for p in acc.positions)
    if held + add_value > lim["gross_exposure_max"] * eq + eps:
        return Verdict(False, f"총노출 한도 초과(>{lim['gross_exposure_max']:.0%})",
                       "hardLimit")
    return Verdict(True, "", "hardLimit")


def safety_check(acc: Account, *, prices_ok: bool, balance_matches: bool) -> Verdict:
    """안전 정지 — 시세 이상·잔고 불일치면 그 사이클 매매를 중단한다."""
    if not prices_ok:
        return Verdict(False, "시세 데이터 이상")
    if not balance_matches:
        return Verdict(False, "잔고 불일치(KIS↔내부)")
    return Verdict(True)


# ── A.1 검사 순서 · 충돌 처리 (결정론 절차) ────────────────────────────────
@dataclass
class MarketState:
    """사이클 시작 시점의 시장·시스템 상태 플래그.

    잔고 대조는 2단이다(05-risk 5.2 검사 1). 매매는 주식과 현금을 항상 같이
    움직이므로, 주식이 맞는데 현금만 어긋났다면 그건 매매로 설명할 수 없는 돈 =
    외부 입출금이다. 그래서 보유 대조(`balance_ok`)만 사고로 다루고 현금은
    아래 둘로 분리한다.
    """
    balance_ok: bool = True       # 1-a 보유 종목·수량 대조 (현금은 아래 둘로 분리)
    cash_negative: bool = False   # 1-b 예수금 음수(미수 발생)
    cash_residual: float = 0.0    # 1-b 현금 잔차(실제 − 기대). 유입 +, 유출 −
    halted: bool = False          # 임시휴장·반장 마감 후
    sidecar: bool = False         # 사이드카 발동 중
    market_cb: bool = False       # KRX 시장 전체 서킷브레이커
    prices_ok: bool = True        # 시세 신선도·이상 없음


@dataclass
class StockStatus:
    """종목별 매매 가능 상태 플래그. 전부 False면 정상."""
    limit_up: bool = False        # 점상한가(호가 소멸)
    limit_down: bool = False      # 점하한가(호가 소멸)
    suspended: bool = False       # 거래정지·관리·투자경고/위험
    vi: bool = False              # VI 발동 중(이번 사이클만 회피)
    overheated: bool = False      # 단기과열(30분 단일가)

    @property
    def limit_lock(self) -> bool:
        """점상·점하 어느 쪽이든 호가가 소멸한 상태인가."""
        return self.limit_up or self.limit_down

    @property
    def block_reason(self) -> str:
        """CycleScores.BlockReason 값. 정상이면 빈 문자열(검사 순서대로 첫 사유 하나)."""
        if self.limit_up:
            return "limitUp"
        if self.limit_down:
            return "limitDown"
        if self.suspended:
            return "halted"
        if self.vi:
            return "vi"
        if self.overheated:
            return "overheated"
        return ""


# risk_checks.check_name → 5.2 검사 순서 번호. 여러 규칙이 동시에 걸려도
# 이 순서에서 가장 먼저 걸린 하나만 사유로 남긴다(05-risk 5.2 / 07-model 7.2).
CHECK_ORDER = {
    "balanceSync": 1,       # 1-a 보유 대조 + 1-b 현금 검사 중 정지에 해당하는 것
    "cashFlow": 1,          # 1-b 중 "기록하고 그대로 진행" — 차단이 아니라 기록이다
    "marketHalt": 2,
    "dataFreshness": 3,
    "circuitBreaker": 4,
    "schema": 5,
    "hardLimit": 6,
    "symbolState": 7,
}


# 유출 전 총자산 대비 이 비율을 넘는 현금 유출은 사고로 본다(05-risk 5.2).
# 튜닝 손잡이가 아니라 "사람을 부르는 선"이라 risk_params.toml에 두지 않는다 —
# 매매 결정을 바꾸는 값만 손잡이가 되고 7개 제약(09-eval 9.3)에 들어간다.
_OUTFLOW_SAFESTOP = 0.50


@dataclass
class CycleDecision:
    """사이클 레벨 판정. action ∈ {proceed, new_blocked, skip, halt}."""
    action: str
    reason: str = ""
    check: str = "balanceSync"   # 판정을 낸 검사 이름(risk_checks.check_name)
    result: str = "pass"         # RiskChecks.Result


def screen_cycle(market: MarketState, acc: Account, params: dict) -> CycleDecision:
    """사이클 레벨 게이트 — 잔고·시장·서킷브레이커를 순서대로 검사해 판정한다.

    현금 잔차가 위 두 SafeStop(미수·대형 유출)에 안 걸리면 여기서는 **아무것도 하지
    않는다.** CashFlows 기록·기준선 재동기화·알림은 호출부(pipeline)의 책임이다 —
    이 파일은 부수효과 없는 순수 판정 함수로 남긴다.
    """
    if not market.balance_ok:                                   # 1-a 보유 대조
        return CycleDecision("halt", "보유 불일치(KIS↔내부)", "balanceSync", "safeStop")
    if market.cash_negative:                                    # 1-b 미수
        return CycleDecision("halt", "예수금 음수(미수 발생)", "balanceSync", "safeStop")
    outflow = -market.cash_residual                             # 1-b 대형 유출
    pre = acc.equity + outflow
    if outflow > 0 and pre > 0 and outflow / pre > _OUTFLOW_SAFESTOP:
        return CycleDecision("halt", f"대형 현금 유출(>{_OUTFLOW_SAFESTOP:.0%}, 사고 의심)",
                             "balanceSync", "safeStop")
    if market.halted:                                           # 2 시장 마비
        return CycleDecision("skip", "임시휴장·반장", "marketHalt", "skipCycle")
    if market.sidecar:
        return CycleDecision("skip", "사이드카 발동", "marketHalt", "skipCycle")
    if market.market_cb:
        return CycleDecision("skip", "KRX 시장 서킷브레이커", "marketHalt", "skipCycle")
    if not market.prices_ok:                                    # 3 데이터 이상
        return CycleDecision("halt", "시세 데이터 이상", "dataFreshness", "safeStop")
    tripped = breakers_tripped(acc, params)                     # 4 우리측 서킷브레이커
    if tripped:
        return CycleDecision("new_blocked", f"서킷브레이커: {','.join(sorted(tripped))}",
                             "circuitBreaker", "reject")
    return CycleDecision("proceed", "", "circuitBreaker", "pass")


def screen_order(
    acc: Account, code: str, add_value: float, status: StockStatus, params: dict,
) -> Verdict:
    """개별 신규매수 주문 게이트(하드룰 → 종목상태 순)."""
    v = check_new_buy(acc, code, add_value, params)             # 6 하드룰 한도
    if not v:
        return v
    if status.limit_lock:                                       # 7 종목 상태
        return Verdict(False, "점상/점하(호가 소멸)", "symbolState")
    if status.suspended:
        return Verdict(False, "거래정지·관리·투자경고", "symbolState")
    if status.vi:
        return Verdict(False, "VI 발동 중", "symbolState")
    if status.overheated:
        return Verdict(False, "단기과열(단일가)", "symbolState")
    return Verdict(True, "", "symbolState")


# ── A.3 모델 이상행동 임계 (결정론 킬스위치) ───────────────────────────────
@dataclass
class OrderProposal:
    """제안 주문(전략 출력). value=주문 금액(원)."""
    code: str
    side: str        # "buy" | "sell"
    value: float


def detect_anomaly(proposals: list[OrderProposal], acc: Account, params: dict) -> Verdict:
    """모델 이상행동을 감지한다 — 하나라도 걸리면 SafeStop(전체 정지)."""
    a = params["anomaly"]
    eq = acc.equity
    if eq <= 0:
        return Verdict(False, "자본 0 이하")
    for p in proposals:
        if p.value > a["single_order_pct"] * eq + 1e-6:
            return Verdict(False, f"단일주문 노출 {a['single_order_pct']:.0%} 초과({p.code})")
    # 신규 주문 수 상한 = 자본 비례식과 동시보유 상한(max_positions) 중 큰 값(소액 오판 방지)
    new_buys = [p for p in proposals if p.side == "buy"]
    limit = max(
        params["limits"]["max_positions"],
        a["max_new_orders_per_capital"] * (eq / a["order_count_capital_base"]),
    )
    if len(new_buys) > limit + 1e-9:
        return Verdict(False, f"신규 진입 주문 폭주({len(new_buys)}건 > {limit:.1f})")
    buys = {p.code for p in proposals if p.side == "buy"}
    sells = {p.code for p in proposals if p.side == "sell"}
    conflict = buys & sells
    if conflict:
        return Verdict(False, f"동일종목 매수·매도 충돌({sorted(conflict)[0]})")
    return Verdict(True)


# ── A.2 재개 · 복구 절차 ────────────────────────────────────────────────
def can_auto_resume(
    breaker: str, *, error_rate_ok: bool = False,
) -> bool:
    """서킷브레이커별 자동 재개 가능 여부를 판정한다(시스템 신뢰성 문제는 항상 사람 개입)."""
    if breaker == "daily_loss":
        return True
    if breaker == "api_error":
        return error_rate_ok
    return False
