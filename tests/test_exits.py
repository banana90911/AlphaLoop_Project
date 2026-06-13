"""청산 규칙 — 우선순위·R 고정 (exec/exits, 05-risk §129)."""
from exec.exits import Position, decide_exit

_P = {"exits": {"tp1_R": 1.5, "tp1_frac": 0.4, "trail_k": 2.75,
                "max_hold_days": 20, "min_progress_R": 0.5}}


def _pos(**kw):
    base = dict(entry_price=10_000, initial_stop=9_000, current_stop=9_000, days_held=1)
    base.update(kw)
    return Position(**base)


def test_thesis_invalid_first_priority():
    # 손절도 깨졌지만 논지무효가 우선
    a = decide_exit(_pos(thesis_valid=False), price=8_000, atr=200, params=_P)
    assert a.action == "exit_full" and a.reason == "thesis_invalid"


def test_invalidation_price_triggers():
    a = decide_exit(_pos(invalidation_price=9_500), price=9_400, atr=200, params=_P)
    assert a.action == "exit_full" and a.reason == "thesis_invalid"


def test_stop_hit():
    a = decide_exit(_pos(), price=8_900, atr=200, params=_P)
    assert a.action == "exit_full" and a.reason == "stop_hit"


def test_tp1_partial_and_breakeven():
    # R=1000, +1.5R=11,500 도달 → 부분익절 + 손절 본전(10,000)
    a = decide_exit(_pos(), price=11_500, atr=200, params=_P)
    assert a.action == "exit_partial"
    assert a.fraction == 0.4
    assert a.new_stop == 10_000


def test_tp1_skipped_if_done():
    # 이미 tp1 완료 → tp1 건너뛰고 트레일링으로
    a = decide_exit(_pos(tp1_done=True, current_stop=10_000), price=11_500, atr=200, params=_P)
    assert a.action == "raise_stop"


def test_trailing_raises_stop():
    # price 12,000, trail 2.75*200=550 → new_stop 11,450 > current 9,000
    a = decide_exit(_pos(tp1_done=True), price=12_000, atr=200, params=_P)
    assert a.action == "raise_stop"
    assert a.new_stop == 12_000 - 2.75 * 200


def test_time_exit_when_stale():
    # 21일 보유, 제자리(+0.5R=10,500 미만), 트레일링 갱신 없음
    a = decide_exit(_pos(days_held=21, current_stop=9_500), price=10_200, atr=300, params=_P)
    # 트레일링 new_stop=10,200-825=9,375 < current 9,500 → 갱신 없음 → 시간청산
    assert a.action == "exit_full" and a.reason == "time_exit"


def test_no_time_exit_if_trending():
    # 보유 21일이어도 +0.5R 넘었으면 시간청산 면제(여기선 트레일링이 잡음)
    a = decide_exit(_pos(days_held=21, tp1_done=True), price=11_000, atr=100, params=_P)
    assert a.action != "exit_full"


def test_hold():
    a = decide_exit(_pos(tp1_done=True, current_stop=9_900), price=9_950, atr=100, params=_P)
    # 손절 위, tp1 완료, 트레일링 new=9,950-275=9,675<9,900, 보유 1일 → hold
    assert a.action == "hold"
