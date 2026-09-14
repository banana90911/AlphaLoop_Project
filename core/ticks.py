"""
description:        KRX 호가가격단위 (주문 가격 정렬)
author:             siheon jung
created date:       2026/09/14
remarks:            거래소가 받아주지 않는 가격으로 주문하면 거부된다. 손절가는
                    `종가 − k×ATR`이라 거의 항상 단위에 안 맞으므로, 브로커로 나가는
                    가격은 전부 여기를 거친다. 2023-01-25 개정표 기준 — KRX가 다시
                    바꾸면 이 표만 고치면 된다.
"""

# (하한가, 호가단위) — 가격이 속한 구간은 [하한, 다음 하한)이다.
# 코스피·코스닥·코넥스가 같은 표를 쓴다.
_TIERS: tuple[tuple[int, int], ...] = (
    (0, 1),
    (2_000, 5),
    (5_000, 10),
    (20_000, 50),
    (50_000, 100),
    (200_000, 500),
    (500_000, 1_000),
)


def tick_size(price: float) -> int:
    """그 가격대의 호가단위(원)를 반환한다."""
    size = _TIERS[0][1]
    for lower, tick in _TIERS:
        if price >= lower:
            size = tick
        else:
            break
    return size


def align_down(price: float) -> int:
    """호가단위에 맞춰 내림한 가격(원)을 반환한다.

    **롱 손절가는 내림이 맞다.** 올림하면 손절선이 진입가 쪽으로 당겨져, 모델이
    의도한 것보다 일찍 털린다. 내림은 R을 아주 조금 키우는 대신 의도를 보존한다.
    """
    if price <= 0:
        return 0
    tick = tick_size(price)
    return int(price // tick * tick)


def align_up(price: float) -> int:
    """호가단위에 맞춰 올림한 가격(원)을 반환한다."""
    if price <= 0:
        return 0
    tick = tick_size(price)
    aligned = int(-(-price // tick) * tick)
    # 올림이 다음 구간으로 넘어가면 그 구간의 단위로 다시 맞춘다
    # (예: 19,995 → 20,000은 50원 단위 구간이라 그대로 유효하다)
    if tick_size(aligned) != tick:
        return int(aligned // tick_size(aligned) * tick_size(aligned))
    return aligned


def is_aligned(price: float) -> bool:
    """그 가격이 호가단위에 맞는지 — 주문 전 검증·테스트용."""
    return price > 0 and float(price) == float(align_down(price))
