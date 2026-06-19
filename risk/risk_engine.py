"""리스크 엔진 — 파산 방지 결정론 바닥 (risk/risk_engine, 05-risk 5-1·A).

주문을 내기 전 최종 *검문소*. LLM/전략 제안을 거부하거나 시스템을 정지시키는 하드룰.
전부 결정론(LLM 위임 금지). 입력(계좌·제안·시장상태)을 받아 허용/차단을 판정하는 순수
로직이라, 실시간 사이클(pipeline)·실데이터가 붙기 전에도 단위 검증이 가능하다.

본 모듈은 5-1 A의 **하드 한도·서킷브레이커·안전 정지**를 담는다(검사 순서 A.1·재개 A.2·
이상행동 A.3는 후속 단계). 한도값은 config/risk_params.toml(그 시점 값으로 과거 재현).

종목당 한도는 둘이다(혼동 주의) — *유도 한도*(per_name_pct≈13%, 사이징이 권장선으로 쓰는
값, 5-2)와 *하드 상한*(per_name_hard_pct=25%, 여기서 절대 차단, A.1·A.3). 별개 개념.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Position:
    """보유 종목 스냅샷. last_price=MTM(시가평가) 단가."""
    code: str
    sector: str
    qty: int
    last_price: float
    market: str = "KOSPI"

    @property
    def value(self) -> float:
        return self.qty * self.last_price


@dataclass
class Account:
    """계좌 스냅샷. start_capital=당일 시작 자본(5-1 A 측정 기준선), peak_equity=드로다운 고점."""
    start_capital: float
    cash: float
    positions: list[Position] = field(default_factory=list)
    peak_equity: float = 0.0

    @property
    def equity(self) -> float:
        return self.cash + sum(p.value for p in self.positions)

    def position_value(self, code: str) -> float:
        return sum(p.value for p in self.positions if p.code == code)

    def sector_value(self, sector: str) -> float:
        return sum(p.value for p in self.positions if p.sector == sector)


@dataclass
class Verdict:
    """판정 결과. allowed=False면 reason에 단일 사유(감사 추적의 결정성, A.1)."""
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def daily_loss_pct(acc: Account) -> float:
    """당일 손익률 = 평가액/당일 시작 자본 − 1 (실현+미실현 MTM, 5-1 A). 음수=손실."""
    if acc.start_capital <= 0:
        return 0.0
    return acc.equity / acc.start_capital - 1.0


def drawdown_pct(acc: Account) -> float:
    """고점 대비 낙폭 = 평가액/고점 − 1 (음수). 고점은 기록 고점과 현재 중 큰 값."""
    peak = max(acc.peak_equity, acc.equity)
    if peak <= 0:
        return 0.0
    return acc.equity / peak - 1.0


def breakers_tripped(acc: Account, params: dict) -> set[str]:
    """발동된 서킷브레이커 집합(비면 정상). 일일 손실·드로다운 (5-1 A·A.2)."""
    lim = params["limits"]
    cb = params.get("circuit_breaker", {})
    tripped: set[str] = set()
    if daily_loss_pct(acc) <= -lim["daily_loss_pct"]:
        tripped.add("daily_loss")
    dd_halt = cb.get("drawdown_halt_pct")
    if dd_halt is not None and drawdown_pct(acc) <= -dd_halt:
        tripped.add("drawdown")
    return tripped


def check_new_buy(
    acc: Account, code: str, sector: str, add_value: float, params: dict
) -> Verdict:
    """신규/추가 매수 add_value(원)가 하드 한도(종목당·섹터·총노출)를 넘는지 (A.1 6번).

    *덜 회복 가능한 것 먼저* 순서로 검사해 첫 위반 하나로 판정(단일 사유 기록).
    """
    lim = params["limits"]
    eq = acc.equity
    if eq <= 0:
        return Verdict(False, "자본 0 이하")
    if add_value <= 0:
        return Verdict(False, "매수 금액 0 이하")
    eps = 1e-6
    name_cap = lim["per_name_hard_pct"] * eq
    if acc.position_value(code) + add_value > name_cap + eps:
        return Verdict(False, f"종목당 한도 초과(>{lim['per_name_hard_pct']:.0%})")
    sec_cap = lim["sector_pct"] * eq
    if acc.sector_value(sector) + add_value > sec_cap + eps:
        return Verdict(False, f"섹터 한도 초과(>{lim['sector_pct']:.0%})")
    held = sum(p.value for p in acc.positions)
    if held + add_value > lim["gross_exposure_max"] * eq + eps:
        return Verdict(False, f"총노출 한도 초과(>{lim['gross_exposure_max']:.0%})")
    return Verdict(True)


def safety_check(acc: Account, *, prices_ok: bool, balance_matches: bool) -> Verdict:
    """안전 정지 — 시세 이상·잔고 불일치면 그 사이클 매매 중단 (5-1). 둘 다 정상이어야 통과."""
    if not prices_ok:
        return Verdict(False, "시세 데이터 이상")
    if not balance_matches:
        return Verdict(False, "잔고 불일치(KIS↔내부)")
    return Verdict(True)


# ── A.1 검사 순서 · 충돌 처리 (결정론 절차) ────────────────────────────────
@dataclass
class MarketState:
    """사이클 시작 시점의 시장·시스템 상태 플래그(데이터 공급은 추후 broker/data)."""
    balance_ok: bool = True       # KIS 실잔고 ↔ 내부 잔고 일치 (A.1 1)
    halted: bool = False          # 임시휴장·반장 마감 후 (A.1 2)
    sidecar: bool = False         # 사이드카 발동 중 (A.1 2)
    market_cb: bool = False       # KRX 시장 전체 서킷브레이커 (A.1 2)
    prices_ok: bool = True        # 시세 신선도·이상 없음 (A.1 3)


@dataclass
class StockStatus:
    """종목별 매매 가능 상태 플래그 (A.1 7). 전부 False면 정상."""
    limit_lock: bool = False      # 점상/점하(호가 소멸)
    suspended: bool = False       # 거래정지·관리·투자경고/위험
    vi: bool = False              # VI 발동 중(이번 사이클만 회피)
    overheated: bool = False      # 단기과열(30분 단일가)


@dataclass
class CycleDecision:
    """사이클 레벨 판정. action ∈ {proceed, new_blocked, skip, halt}."""
    action: str
    reason: str = ""


def screen_cycle(market: MarketState, acc: Account, params: dict) -> CycleDecision:
    """A.1 1~4: 사이클 레벨 게이트. *덜 회복 가능한 것 먼저*, 첫 위반 단일 사유로 판정.

    - halt: 잔고/시세 이상 → 매매 중단(보유 청산도 보류, 안전정지)
    - skip: 시장 마비 → 사이클 완전 스킵
    - new_blocked: 우리측 서킷브레이커 발동 → 신규만 차단(보유 청산은 정상)
    - proceed: 정상
    """
    if not market.balance_ok:                                   # 1 선행 게이트
        return CycleDecision("halt", "잔고 불일치(KIS↔내부)")
    if market.halted:                                           # 2 시장 마비
        return CycleDecision("skip", "임시휴장·반장")
    if market.sidecar:
        return CycleDecision("skip", "사이드카 발동")
    if market.market_cb:
        return CycleDecision("skip", "KRX 시장 서킷브레이커")
    if not market.prices_ok:                                    # 3 데이터 이상
        return CycleDecision("halt", "시세 데이터 이상")
    tripped = breakers_tripped(acc, params)                     # 4 우리측 서킷브레이커
    if tripped:
        return CycleDecision("new_blocked", f"서킷브레이커: {','.join(sorted(tripped))}")
    return CycleDecision("proceed")


def screen_order(
    acc: Account, code: str, sector: str, add_value: float,
    status: StockStatus, params: dict, *, liquidity_ok: bool = True,
) -> Verdict:
    """A.1 6~8: 개별 신규매수 주문 게이트(하드룰 → 종목상태 → 유동성). 첫 위반 단일 사유."""
    v = check_new_buy(acc, code, sector, add_value, params)     # 6 하드룰 한도
    if not v:
        return v
    if status.limit_lock:                                       # 7 종목 상태
        return Verdict(False, "점상/점하(호가 소멸)")
    if status.suspended:
        return Verdict(False, "거래정지·관리·투자경고")
    if status.vi:
        return Verdict(False, "VI 발동 중")
    if status.overheated:
        return Verdict(False, "단기과열(단일가)")
    if not liquidity_ok:                                        # 8 유동성 한도
        return Verdict(False, "유동성 한도(ADV) 초과")
    return Verdict(True)


# ── A.3 모델 이상행동 임계 (결정론 킬스위치) ───────────────────────────────
@dataclass
class OrderProposal:
    """제안 주문(LLM/전략 출력). value=주문 금액(원)."""
    code: str
    side: str        # "buy" | "sell"
    value: float


def detect_anomaly(proposals: list[OrderProposal], acc: Account, params: dict) -> Verdict:
    """A.3: 모델 이상행동 감지 → 하나라도 걸리면 SafeStop(전체 정지·사람 개입).

    버그로 인한 주문 폭주·논리 모순을 싸고 단순하게 차단(임계는 config·튜닝).
    """
    a = params["anomaly"]
    eq = acc.equity
    if eq <= 0:
        return Verdict(False, "자본 0 이하")
    # 단일 주문 노출이 자본의 single_order_pct 초과 (종목당 하드룰 우회 시도 = 이상 신호)
    for p in proposals:
        if p.value > a["single_order_pct"] * eq + 1e-6:
            return Verdict(False, f"단일주문 노출 {a['single_order_pct']:.0%} 초과({p.code})")
    # 신규 진입 주문 수 폭주 (자본 비례 절대 상한)
    new_buys = [p for p in proposals if p.side == "buy"]
    limit = a["max_new_orders_per_capital"] * (eq / a["order_count_capital_base"])
    if len(new_buys) > limit + 1e-9:
        return Verdict(False, f"신규 진입 주문 폭주({len(new_buys)}건 > {limit:.1f})")
    # 동일 종목 매수·매도 동시 제안 (방향 충돌 = 명백한 버그)
    buys = {p.code for p in proposals if p.side == "buy"}
    sells = {p.code for p in proposals if p.side == "sell"}
    conflict = buys & sells
    if conflict:
        return Verdict(False, f"동일종목 매수·매도 충돌({sorted(conflict)[0]})")
    return Verdict(True)


# ── A.2 재개 · 복구 절차 ────────────────────────────────────────────────
def can_auto_resume(
    breaker: str, *, recovered_to_half: bool = False,
    error_rate_ok: bool = False, deadlock: bool = False,
) -> bool:
    """A.2: 서킷브레이커별 자동 재개 가능 여부. 시스템 신뢰성 문제(SafeStop류)는 항상 사람 개입.

    - daily_loss: 당일 중단·날짜 경계에서 자동 리셋(호출측이 날짜 판정) → True
    - drawdown: 한도의 50% 아래로 자연 회복 시 자동, 단 손절 데드락이면 사람
    - api_error: 오류율 50% 아래 30분 유지 시 자동
    - 그 외(safe_stop·잔고불일치·데이터오류·모델이상): 사람 개입 필수 → False
    """
    if breaker == "daily_loss":
        return True
    if breaker == "drawdown":
        return recovered_to_half and not deadlock
    if breaker == "api_error":
        return error_rate_ok
    return False
