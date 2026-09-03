"""
description:        워크포워드 OOS 분할 (시간순 train/test 구간 나누기)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/08/30
remarks:
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Split:
    """한 워크포워드 구간. train으로 튜닝, test(OOS)로만 평가."""
    train_start: date
    train_end: date          # 포함(inclusive)
    test_start: date
    test_end: date           # 포함(inclusive)


def rolling_splits(
    dates: list[date],
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
    anchored: bool = False,
) -> list[Split]:
    """거래일 수 기준으로 워크포워드 구간을 분할한다(train 뒤에 test가 OOS로 붙음)."""
    if train_size < 1 or test_size < 1:
        raise ValueError("train_size·test_size ≥ 1")
    step = step or test_size
    n = len(dates)
    splits: list[Split] = []
    i = 0
    while i + train_size + test_size <= n:
        train_lo = 0 if anchored else i
        train_hi = i + train_size            # exclusive
        test_hi = train_hi + test_size       # exclusive
        splits.append(Split(
            dates[train_lo], dates[train_hi - 1],
            dates[train_hi], dates[test_hi - 1],
        ))
        i += step
    return splits


def concat_oos_returns(per_split_oos: list) -> list:
    """각 split의 OOS 수익 시퀀스를 시간순으로 이어 붙인다."""
    out: list = []
    for seq in per_split_oos:
        out.extend(seq)
    return out
