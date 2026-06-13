"""워크포워드 OOS 분할 (backtest/walkforward, 10-1).

과최적화 방지의 핵심: 데이터를 시간순으로 *튜닝(IS)* 과 *검증(OOS, 손대지 않는)* 으로 나누고,
롤링으로 전진하며 **검증 구간 성과만** Go/No-Go에 쓴다. 검증 구간을 보고 파라미터를 되돌려
고치지 않는다(고치면 새 검증 구간에서만 다시 평가).

분할은 순수 함수(날짜 리스트 → 구간 목록). 실제 튜닝/평가 실행은 engine·gate와 결합한다.
- rolling: 고정 크기 train 윈도우가 전진(과거를 버림)
- anchored: train 시작점 고정, 윈도우가 확장(모든 과거 누적)
"""
from __future__ import annotations

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
    """거래일 수 기준 워크포워드 분할. train_size 다음에 test_size가 OOS로 붙는다.

    step(기본=test_size)만큼 전진. anchored=True면 train 시작을 0에 고정(확장 윈도우).
    train과 test는 겹치지 않으며, test는 항상 train 직후 미래 구간(룩어헤드 없음).
    """
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
    """각 split의 OOS 수익 시퀀스를 시간순으로 이어 붙인다(전체 OOS 곡선 구성용)."""
    out: list = []
    for seq in per_split_oos:
        out.extend(seq)
    return out
