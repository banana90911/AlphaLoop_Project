"""인출 스코어러 — 구조화 매칭 (memory/retrieval, 07 7.8·7.18·7.21·P1-6).

"오늘과 닮은 과거 상황"을 골라 결정자에 주입한다. 점수 = 레짐 유사도 + 섹터 + 셋업 +
최근성 + 보정신뢰의 가중 합(임베딩/RAG 미사용 — 자가생성 정형 기억엔 구조화 매칭이 적합).

초기 휴면(정직 고지, 7.21): 회고가 보류라 `lessons`가 0건이면 꺼낼 게 없다. 그 공백을
**과거 `decisions`↔`outcomes` 원시 매칭**으로 메운다(P1-6) — 같은 레짐·섹터·셋업인 과거
결정의 실제 손익을 압축 요약해 주입한다. 빈 결과는 정상(데이터가 쌓이며 작동).
"""
from __future__ import annotations

import math
import sqlite3
from collections import Counter
from datetime import date

import numpy as np


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """레짐 벡터 코사인 유사도 −1~1 (7.8). 한쪽이 영벡터면 0."""
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(va @ vb / (na * nb))


def regime_similarity(a: list[float], b: list[float]) -> float:
    """레짐 벡터 유사도 0~1 (코사인을 0~1로 정규화)."""
    return (cosine_similarity(a, b) + 1.0) / 2.0


def recency_weight(age_days: float, half_life: float = 180.0) -> float:
    """최근성 가중 0~1 (7.15). 최근(age 0)=1, 반감기마다 절반."""
    return float(math.exp(-math.log(2) / half_life * max(age_days, 0.0)))


def retrieval_score(
    *, regime_sim: float, recency: float, sector_match: float = 0.5,
    setup_match: float = 0.5, calib: float = 0.5, weights: dict,
) -> float:
    """가중 합산 인출 점수 0~1 (7.3·7.18). 결측 차원은 중립(0.5)."""
    parts = [
        (weights["w_regime"], regime_sim),
        (weights["w_sector"], sector_match),
        (weights["w_setup"], setup_match),
        (weights["w_recency"], recency),
        (weights["w_calib"], calib),
    ]
    wsum = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / wsum if wsum else 0.0


def summarize_outcomes(rows: list[dict]) -> str:
    """인출된 과거 결과를 압축 요약(거짓 정밀 방지 — 표본수 동반, 7.21)."""
    if not rows:
        return "유사 과거 사례 없음"
    rets = [r["return_pct"] for r in rows if r.get("return_pct") is not None]
    n = len(rows)
    if not rets:
        return f"유사 {n}건(수익 데이터 없음)"
    avg = sum(rets) / len(rets)
    win = sum(1 for x in rets if x > 0) / len(rets)
    reasons = [r["exit_reason"] for r in rows if r.get("exit_reason")]
    top = Counter(reasons).most_common(1)[0][0] if reasons else "미상"
    return f"유사 {n}건: 평균 {avg:+.1%}, 승률 {win:.0%}, 주된 청산 {top}"


def _age_days(closed_at: str, today: date) -> int:
    try:
        return max((today - date.fromisoformat(closed_at[:10])).days, 0)
    except (ValueError, TypeError):
        return 0


def retrieve(
    conn: sqlite3.Connection, today_regime: str, *, weights: dict,
    source: str = "live", top_k: int = 5, half_life: float = 180.0,
    calib: float = 0.5, today: date | None = None,
) -> list[dict]:
    """과거 decisions↔outcomes 원시 매칭 인출(P1-6). 점수순 상위 top_k. 빈 결과 정상.

    레짐은 현재 regime_tag 문자열 일치(레짐 벡터가 쌓이면 cosine으로 정밀화). 섹터·셋업은
    데이터 매핑 전이라 중립(0.5)으로 둔다(7.21 초기 휴면 — 데이터 쌓이며 활성).
    """
    today = today or date.today()
    rows = conn.execute(
        "SELECT d.regime_tag, o.return_pct, o.exit_reason, o.closed_at, o.symbol "
        "FROM outcomes o JOIN decisions d ON o.entry_decision_id = d.decision_id "
        "WHERE o.source = ?",
        (source,),
    ).fetchall()
    scored: list[tuple[float, dict]] = []
    for regime_tag, return_pct, exit_reason, closed_at, symbol in rows:
        sim = 1.0 if regime_tag == today_regime else 0.0
        rec = recency_weight(_age_days(closed_at, today), half_life)
        score = retrieval_score(regime_sim=sim, recency=rec, calib=calib, weights=weights)
        scored.append((score, {
            "regime_tag": regime_tag, "return_pct": return_pct,
            "exit_reason": exit_reason, "closed_at": closed_at,
            "symbol": symbol, "score": score,
        }))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:top_k]]
