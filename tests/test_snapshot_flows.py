"""
description:        계좌 스냅샷의 누적 순입금 — 수동 등록 흐름 반영 (05-risk 5.2 / 09-eval)
author:             siheon jung
created date:       2026/09/14
remarks:            2026-09-14 실계좌에서 발견: CashFlows에 confirmed deposit 20만원이
                    있는데 스냅샷의 CumulativeNetFlow가 0이라 원금 전액이 수익으로
                    잡혔다(당일수익률 +100%). 증분만 이어받던 것이 원인이라
                    `ops.cashflow add`로 넣은 흐름이 영영 반영되지 않았다.
"""

from datetime import date

from memory import journal

_DAY = date(2026, 9, 14)


def _snap(conn, cycle_id: str, *, total: float, cash: float | None = None,
          flow: float = 0.0, base: float | None = None) -> dict:
    journal.create_cycle(conn, cycle_id, trade_date=_DAY, mode="real")
    journal.record_account_snapshot(
        conn, cycle_id=cycle_id, cash=cash if cash is not None else total,
        position_value=0.0, total_asset=total, base_asset=base,
        flow_this_snapshot=flow, trade_date=_DAY, mode="real",
    )
    return journal.last_account_snapshot(conn)


def _deposit(conn, amount: float, note: str) -> str:
    """`ops.cashflow add`가 남기는 것과 같은 수동 확정 흐름."""
    return journal.record_cash_flow(
        conn, None, kind="deposit", amount=amount, source="manual",
        expected=0.0, actual=0.0, mode="real", status="confirmed",
        note=note, trade_date=_DAY,
    )


def test_수동_등록_입금이_누적에_잡힌다(conn):
    """이게 깨져 있었다. CashFlows에만 있고 스냅샷엔 0으로 남았다."""
    _deposit(conn, 100_000, "개시 자본")
    _deposit(conn, 100_000, "2차 입금")
    row = _snap(conn, "C1", total=200_000)
    assert float(row["cumulative_net_flow"]) == 200_000


def test_누적손익은_원금을_빼고_계산된다(conn):
    """누적손익 = 총자본 − 누적순입금. 원금만 넣은 상태면 0이어야 한다."""
    _deposit(conn, 200_000, "개시 자본")
    row = _snap(conn, "C1", total=200_000)
    profit = float(row["total_asset"]) - float(row["cumulative_net_flow"])
    assert profit == 0.0


def test_첫_스냅샷은_기준선을_밀지_않는다(conn):
    """기준선이 곧 현재 자산이라 그 사이에 흐른 돈이 없다 — 0%여야 한다."""
    _deposit(conn, 200_000, "개시 자본")
    row = _snap(conn, "C1", total=200_000)
    assert float(row["net_flow_since_base"]) == 0.0
    assert float(row["day_return_percent"]) == 0.0


def test_스냅샷_사이의_입금은_수익으로_잡히지_않는다(conn):
    """실계좌에서 +100%로 찍혔던 상황 — 10만원 계좌에 10만원을 더 넣은 날."""
    _deposit(conn, 100_000, "개시 자본")
    _snap(conn, "C1", total=100_000)
    _deposit(conn, 100_000, "2차 입금")
    row = _snap(conn, "C2", total=200_000)
    assert float(row["net_flow_since_base"]) == 100_000     # 기준선이 같이 밀려야
    assert float(row["day_return_percent"]) == 0.0          # 수익이 아니다
    assert float(row["cumulative_net_flow"]) == 200_000


def test_잔차로_감지된_흐름은_이중_계상되지_않는다(conn):
    """사이클은 스냅샷을 먼저 적고 CashFlows를 나중에 쓴다. 그 사이 값이
    flow_this_snapshot인데, 다음 스냅샷에서 또 더해지면 안 된다."""
    _snap(conn, "C1", total=100_000)
    # 이번 사이클이 잔차 10만원을 감지 — 아직 CashFlows엔 없다
    row = _snap(conn, "C2", total=200_000, flow=100_000)
    assert float(row["cumulative_net_flow"]) == 100_000
    # 사이클이 뒤늦게 CashFlows에 기록한다
    journal.record_cash_flow(
        conn, "C2", kind="deposit", amount=100_000, source="residual",
        expected=100_000, actual=200_000, mode="real", status="unconfirmed",
        trade_date=_DAY,
    )
    row = _snap(conn, "C3", total=200_000)
    assert float(row["cumulative_net_flow"]) == 100_000     # 두 번 세지 않는다
    assert float(row["net_flow_since_base"]) == 0.0         # 새 흐름도 아니다


def test_배당은_외부흐름이_아니라_수익이다(conn):
    """입금은 수익률에서 빼지만 배당은 수익으로 남아야 한다(09-eval)."""
    _deposit(conn, 200_000, "개시 자본")
    journal.record_cash_flow(
        conn, None, kind="dividend", amount=5_000, source="manual",
        expected=0.0, actual=0.0, mode="real", status="confirmed", trade_date=_DAY,
    )
    row = _snap(conn, "C1", total=205_000)
    assert float(row["cumulative_net_flow"]) == 200_000     # 배당은 안 들어간다
    profit = float(row["total_asset"]) - float(row["cumulative_net_flow"])
    assert profit == 5_000                                  # 배당이 수익으로 남는다


def test_수정_전에_쌓인_행은_다음_스냅샷에서_한_번에_따라잡는다(conn):
    """호환 동작을 명시해 둔다.

    고치기 전 스냅샷은 누적이 0으로 남아 있다. 배포 뒤 첫 스냅샷은 그 차이를
    한꺼번에 새 외부흐름으로 인식하므로, **누적은 즉시 맞지만 그 하루의 손익률은
    의미가 없다.** 과거 행의 누적을 손으로 보정하면 이 한 번도 사라진다.
    """
    _deposit(conn, 200_000, "개시 자본")
    journal.create_cycle(conn, "OLD", trade_date=_DAY, mode="real")
    # 옛 구현이 남긴 모양 — CashFlows엔 20만원이 있는데 누적은 0
    conn.execute(
        'INSERT INTO account_snapshots(snapshot_id, cycle_id, trade_date, amount, '
        'position_value, total_asset, base_asset, net_flow_since_base, '
        'adjusted_base_asset, cumulative_net_flow, twr_index, day_return_percent, '
        'recorded_date_time) '
        "VALUES('OLD_snap','OLD',%s,200000,0,200000,200000,0,200000,0,1.0,0,now())",
        (_DAY,),
    )
    conn.commit()

    row = _snap(conn, "NEW", total=200_000)
    assert float(row["cumulative_net_flow"]) == 200_000      # 누적은 바로 맞는다
    assert float(row["net_flow_since_base"]) == 200_000      # 그 차이를 한 번에 인식
