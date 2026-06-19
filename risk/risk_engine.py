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
