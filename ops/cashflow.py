"""
description:        외부 현금흐름(입출금) 감지 정책 + 라벨링 CLI
author:             siheon jung
created date:       2026/09/02
last modified date: 2026/09/02
remarks:            대시보드가 아니라 CLI인 이유는 08-dashboard 8.1의 읽기 전용
                    경계 때문이다. 라벨을 붙이는 것은 쓰기라서 대시보드에 두면
                    그 경계가 깨진다. Tailscale + SSH로 폰에서도 같은 명령을 친다.
"""

import argparse
from dataclasses import dataclass
from datetime import date

from config.settings import get_settings
from memory import journal
from memory.db import init_db

# ── 감지 정책 상수 ─────────────────────────────────────────────────────────
# 전부 "매매 결정을 바꾸지 않는" 값이라 risk_params.toml이 아니라 여기 상수로 둔다.
# 어떤 값이 나중에 매매를 바꾸게 되면 그 순간 손잡이로 승격시키고 7개를 다시 센다.

# 흡수 임계 — 이보다 작은 잔차는 이체가 아니라 계산 오차로 본다.
# 0으로 두면 안 되는 이유: 수수료·거래세 원 단위 절사, 예수금 필드가 여러 개인 문제
# (예수금총금액/익일정산금액/가수도정산금액, D+2 결제), 계좌 이자 때문에 잔차는
# 현실에서 절대 0이 안 된다. 1원까지 이체로 기록하면 CashFlows가 쓰레기로 찬다.
ABSORB_MIN_KRW = 1_000.0
ABSORB_EQUITY_RATIO = 0.0001      # 자본의 0.01%
# 흡수 임계의 상한. 자본의 이 비율을 넘는 잔차는 무슨 일이 있어도 "잡음"일 수 없다.
# 상한이 없으면 소액 계좌에서 임계가 자본보다 커진다 — 자본 751원인 계좌의 임계가
# 1,000원이라 전액을 빼도 수수료로 흡수됐다(2026-09-09 실측: 출금이 TWR −100%가 됐다).
ABSORB_MAX_EQUITY_RATIO = 0.05    # 자본의 5%

# 알림 임계 — 관찰 모드에서는 이 값 이상만 Discord로 울린다(자잘한 잔차로 안 깨우려고).
ALERT_EQUITY_RATIO = 0.01         # 자본의 1%

# 금액 서명(선택 기능) — 이체 금액 끝 세 자리를 약속값으로 맞추면 Kind가 자동 확정된다.
SIGNATURE_DEPOSIT = 777
SIGNATURE_WITHDRAWAL = 555
SIGNATURE_MODULUS = 1_000


def absorb_threshold(equity: float) -> float:
    """흡수 임계 = max(1,000원, 자본의 0.01%). 단 **자본의 5%를 넘지 않는다**.

    하한만 있으면 자본이 작을 때 임계가 자본을 넘어서, 그 계좌에서는 어떤 이체도
    원리적으로 감지될 수 없다. 상한은 그 구멍만 막는다 — 자본 2만원 이상에서는
    5% 상한이 1,000원보다 크므로 종전과 동작이 같다.
    """
    floor = max(ABSORB_MIN_KRW, abs(equity) * ABSORB_EQUITY_RATIO)
    return min(floor, abs(equity) * ABSORB_MAX_EQUITY_RATIO)


def alert_threshold(equity: float) -> float:
    """관찰 모드에서 Discord를 울릴 최소 금액 = 자본의 1%."""
    return abs(equity) * ALERT_EQUITY_RATIO


def _signature_kind(amount: float) -> str | None:
    """금액 서명으로 Kind를 읽는다(약속에 안 맞으면 None)."""
    tail = int(round(abs(amount))) % SIGNATURE_MODULUS
    if tail == SIGNATURE_DEPOSIT and amount > 0:
        return "deposit"
    if tail == SIGNATURE_WITHDRAWAL and amount < 0:
        return "withdrawal"
    return None


@dataclass
class FlowResolution:
    """잔차 하나를 어떻게 다룰지에 대한 판정(부수효과 없음)."""
    record: bool        # CashFlows에 남기는가
    absorbed: bool      # 손익으로 흡수하는가(= 기준선을 옮기지 않는다)
    kind: str           # deposit/withdrawal/fee/unknown
    source: str         # residual/signature
    alert: bool         # Discord를 울리는가

    @property
    def shifts_baseline(self) -> bool:
        """이 흐름이 서킷브레이커 기준선을 평행이동시키는가."""
        return not self.absorbed and self.kind in journal.EXTERNAL_KINDS


def classify_residual(
    residual: float,
    equity: float,
    *,
    observation_mode: bool | None = None,
    signature_enabled: bool | None = None,
) -> FlowResolution:
    """현금 잔차를 어떻게 처리할지 정한다(05-risk 5.2 검사 1-b의 마지막 두 갈래).

    흡수 임계 미만은 손익으로 흡수한다 — 그 크기의 잔차는 대다수가 수수료·이자 같은
    실제 손익이지 이체가 아니기 때문이다(09-eval). 다만 임계 미만이어도 **기대 예수금은
    반드시 실제값으로 재동기화**해야 한다. 안 그러면 매일 몇 원씩 쌓여 언젠가 임계를
    넘어 가짜 이체가 감지된다. 그 재동기화는 호출부(pipeline)의 책임이다.
    """
    cfg = get_settings()
    if observation_mode is None:
        observation_mode = cfg.cashflow_observation_mode
    if signature_enabled is None:
        signature_enabled = cfg.cashflow_signature_enabled

    if residual == 0:
        # 어긋난 게 없다. 남길 것도, 기준선을 옮길 것도 없다. 자본이 0일 때는 임계도
        # 0이 되므로, 이 갈래가 없으면 0원 잔차가 '이체'로 분류된다.
        return FlowResolution(record=False, absorbed=True, kind="fee",
                              source="residual", alert=False)

    if abs(residual) < absorb_threshold(equity):
        # 흡수 — 관찰 모드에서는 분포를 모으려고 기록만 남긴다. Kind가 'fee'인 것은
        # 이 크기의 잔차를 수익 계열로 보아 순외부흐름에서 빼기 위해서다.
        return FlowResolution(
            record=observation_mode, absorbed=True, kind="fee",
            source="residual", alert=False,
        )

    kind, source = "unknown", "residual"
    if signature_enabled and (signed := _signature_kind(residual)) is not None:
        kind, source = signed, "signature"

    # 관찰 모드에서는 넉넉한 값에서만 울린다. 임계를 확정한 뒤에는 전부 울린다.
    alert = abs(residual) >= alert_threshold(equity) if observation_mode else True
    return FlowResolution(record=True, absorbed=False, kind=kind, source=source, alert=alert)


# ── CLI ────────────────────────────────────────────────────────────────────
def _fmt(row: dict) -> str:
    sign = "+" if float(row["amount"]) >= 0 else ""
    return (
        f'{row["flow_id"]}  {row["trade_date"]}  {row["kind"]:<10} '
        f'{sign}{float(row["amount"]):>14,.0f}원  {row["status"]:<12} '
        f'{row["source"]:<9} {row["note"] or ""}'
    )


def main(argv: list[str] | None = None, conn=None) -> int:
    """CLI 진입점 — 감지된 외부 현금흐름을 보고 라벨을 붙인다.

    `conn`은 테스트가 임시 DB를 넣기 위한 자리다. 비우면 설정의 저장소에 붙는다.
    """
    ap = argparse.ArgumentParser(
        prog="python -m ops.cashflow",
        description="외부 현금흐름(입출금·배당) 확인과 라벨링",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="흐름 목록(기본: 미확인만)")
    p_list.add_argument("--all", action="store_true", help="확인된 것까지 전부")
    p_list.add_argument("--limit", type=int, default=50)

    # 잔차로 감지되지 않는 흐름을 사람이 직접 남긴다. 두 경우에 필요하다.
    #  ① 개시 시점에 이미 계좌에 있던 돈 — 비교할 직전 스냅샷이 없어 감지되지 않는다.
    #  ② 흡수 임계(max 1,000원, 자본의 0.01%) 미만의 이체 — 수수료로 흡수돼 버린다.
    # 남기지 않으면 원금이 손익으로 계산된다(누적 손익 = 총자본 − 누적 순입금).
    p_add = sub.add_parser("add", help="감지되지 않은 흐름을 직접 등록")
    p_add.add_argument("--kind", required=True, choices=journal.FLOW_KINDS)
    p_add.add_argument("--amount", required=True, type=float,
                       help="부호 있는 금액(유입 +, 유출 −). 예: 입금 751, 출금 -751")
    p_add.add_argument("--date", default=None, help="거래일 YYYY-MM-DD(생략 시 오늘)")
    p_add.add_argument("--note", default=None)

    for name, help_text in (("confirm", "라벨 확정"), ("reclassify", "라벨 정정")):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--id", required=True, help="flow_id")
        sp.add_argument("--kind", required=True, choices=journal.FLOW_KINDS)
        sp.add_argument("--note", default=None)

    args = ap.parse_args(argv)
    conn = conn if conn is not None else init_db()

    if args.cmd == "list":
        rows = journal.load_cash_flows(
            conn, status=None if args.all else "unconfirmed", limit=args.limit
        )
        if not rows:
            print("표시할 현금흐름이 없습니다.")
            return 0
        for r in rows:
            print(_fmt(r))
        return 0

    if args.cmd == "add":
        if args.amount == 0:
            print("금액이 0이면 남길 것이 없습니다.")
            return 1
        if (args.kind == "deposit") != (args.amount > 0):
            print(f"부호가 종류와 어긋납니다: {args.kind}에 {args.amount:+,.0f}원 "
                  "(입금은 +, 출금은 −)")
            if args.kind in ("deposit", "withdrawal"):
                return 1
        trade_date = date.fromisoformat(args.date) if args.date else None
        # 사람이 직접 넣은 것이므로 감지·확정을 한 번에 끝낸다. 기대·실제 예수금은
        # 대조로 얻은 값이 아니라서 0으로 둔다(그 자리에 넣을 근거가 없다).
        flow_id = journal.record_cash_flow(
            conn, None, kind=args.kind, amount=args.amount, source="manual",
            expected=0.0, actual=0.0, mode=get_settings().trading_mode,
            status="confirmed", note=args.note, trade_date=trade_date,
        )
        print(f"등록 완료: {flow_id} · {args.kind} {args.amount:+,.0f}원")
        # 누적 순입금은 화면을 열 때마다 CashFlows를 더해서 낸다(10-ops 10.18).
        # 스냅샷 러닝합을 쓰던 시절의 "다음 사이클부터" 안내는 더 이상 맞지 않는다.
        print("  대시보드 누적 순입금에 바로 반영됩니다(새로고침).")
        return 0

    ok = journal.confirm_cash_flow(conn, args.id, kind=args.kind, by="cli", note=args.note)
    if not ok:
        print(f"해당 FlowId가 없습니다: {args.id}")
        return 1
    print(f"{args.id} → {args.kind} 확정")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
