"""
description:        Discord 웹훅 알림 (조기 경보 + 시크릿 마스킹)
author:             siheon jung
created date:       2026/08/29
last modified date: 2026/09/08
remarks:
"""

import logging
import re
from collections.abc import Sequence
from datetime import date
from typing import NamedTuple

import requests

from config.settings import get_settings

log = logging.getLogger(__name__)

TIMEOUT_S = 5
MAX_LEN = 1900          # Discord 본문 2000자 제한 - 제목·꾸밈 여유

# 심각도 → 표시. 정지·사고는 즉시 확인이 필요하고, 경보는 하루 안에 보면 된다.
LEVELS = ("info", "warning", "critical")
_MARK = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}

# 메시지에 섞여 들어갈 수 있는 시크릿 형태 — 보내기 전에 지운다.
_SECRET_PATTERNS = (
    re.compile(r"\b\d{8}-?\d{2}\b"),                 # 계좌번호 8-2
    re.compile(r"\bPS[A-Za-z0-9]{16,}\b"),           # KIS App Key
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]+", re.I),  # 토큰
)


def _mask(text: str) -> str:
    """시크릿으로 보이는 토막을 가린다."""
    for pat in _SECRET_PATTERNS:
        text = pat.sub("***", text)
    return text


def send(message: str, *, level: str = "info", title: str | None = None) -> bool:
    """Discord 웹훅으로 한 건 보낸다(실패해도 예외를 올리지 않는다). 반환: 성공 여부."""
    if level not in LEVELS:
        level = "info"
    url = get_settings().discord_webhook_url
    if not url:
        log.warning("discord_webhook_url 미설정 — 알림 생략: %s", title or message[:60])
        return False
    head = f"{_MARK[level]} **{title}**\n" if title else f"{_MARK[level]} "
    body = _mask(message)[:MAX_LEN]
    try:
        r = requests.post(url, json={"content": head + body}, timeout=TIMEOUT_S)
        if r.status_code >= 400:
            log.warning("알림 전송 실패 HTTP %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:                    # 알림 실패가 매매를 멈추면 안 된다
        log.warning("알림 전송 예외 %s: %s", type(e).__name__, e)
        return False


def notify_safe_stop(cause: str, cycle_id: str | None = None) -> bool:
    """전체 정지 알림을 보낸다(사람이 풀어줘야 하는 상태)."""
    where = f"\n사이클: `{cycle_id}`" if cycle_id else ""
    return send(
        f"매매를 전체 정지했습니다.\n원인: {cause}{where}\n\n"
        "신규 주문이 차단됩니다(보유 청산은 계속 돕니다). "
        "잔고 불일치·데이터 오류는 확인 후 사람이 직접 해제해야 합니다.",
        level="critical", title="SafeStop 발생",
    )


def notify_cash_flow(
    flow_id: str, amount: float, *, expected: float, actual: float,
    kind: str = "unknown", cycle_id: str | None = None,
) -> bool:
    """외부 현금흐름 감지 알림. 차단이 아니라 "기록했고 그대로 진행했다"는 통지다."""
    direction = "입금" if amount >= 0 else "출금"
    where = f"\n감지 사이클: `{cycle_id}`" if cycle_id else ""
    return send(
        f"{direction} {abs(amount):,.0f}원으로 보이는 현금 변동을 감지했습니다.\n"
        f"기대 예수금: {expected:,.0f}원 / 실제 예수금: {actual:,.0f}원\n"
        f"분류: `{kind}`{where}\n\n"
        "보유 종목·수량은 일치하므로 **매매는 그대로 계속됩니다.** "
        "서킷브레이커 기준선은 이 금액만큼 자동으로 옮겼습니다.\n"
        "라벨만 나중에 붙여주세요:\n"
        f"`python -m ops.cashflow confirm --id {flow_id} --kind deposit`\n"
        "(배당이면 `--kind dividend` — 입금은 수익률에서 빼고 배당은 수익으로 잡습니다.)",
        level="info", title="외부 현금흐름 감지",
    )


class StepLine(NamedTuple):
    """배치 한 단계 요약 — `run_daily_ingest`의 `StepResult`를 알림용으로 줄인 것.

    알림 모듈이 배치 모듈을 import하면 순환이 되므로, 필요한 값만 여기로 옮겨 받는다.
    """
    table: str
    status: str                 # ok / partial / failed
    success: int                # 조회 성공 종목 수 (종목 단위가 아닌 단계는 0)
    target: int                 # 조회 대상 종목 수 (〃)
    rows: int                   # 적재 행 수


_STEP_MARK = {"ok": "✅", "partial": "🔸", "failed": "❌"}
# 심각도 순서·표식 — 여러 측정값 중 가장 나쁜 것이 전체 등급이 된다.
_WORST_LEVEL = ("info", "warning", "critical")
_STEP_MARK_BY_LEVEL = {"info": "✅", "warning": "🔸", "critical": "❌"}
# 단계 하나라도 나쁘면 전체가 그 등급이 된다 — 나쁜 쪽이 이긴다.
_WORST_ORDER = ("ok", "partial", "failed")
_INGEST_TITLE = {
    "ok": "일일 배치 완료", "partial": "일일 배치 부분 성공", "failed": "일일 배치 실패",
}
_INGEST_LEVEL = {"ok": "info", "partial": "warning", "failed": "critical"}


def notify_ingest_summary(
    trade_date: date, steps: Sequence[StepLine], *, mode: str = "real",
) -> bool:
    """일일 배치 결과 요약. 실패만이 아니라 **성공도 매번 보낸다**.

    조용한 성공은 "안 돈 것"과 구별되지 않는다(10-ops 10.4). 하루 한 번뿐이라
    알림이 넘치지도 않는다.
    """
    worst = "ok"
    for s in steps:
        if _WORST_ORDER.index(s.status) > _WORST_ORDER.index(worst):
            worst = s.status
    lines = []
    for s in steps:
        scope = f"{s.success:,}/{s.target:,}종목 · " if s.target else ""
        lines.append(f"{_STEP_MARK.get(s.status, '·')} `{s.table}` {scope}{s.rows:,}행")
    tail = "" if worst == "ok" else (
        "\n\n오늘 사이클은 데이터 신선도 검사에서 멈출 수 있습니다. "
        "`python run_daily_ingest.py --resume`으로 못 받은 종목만 재시도하세요."
    )
    return send(
        f"거래일 {trade_date} · `{mode}`\n" + "\n".join(lines) + tail,
        level=_INGEST_LEVEL[worst], title=_INGEST_TITLE[worst],
    )


class TradeLine(NamedTuple):
    """사이클이 실제로 낸 매매 한 건 — `orders` 한 행을 알림용으로 줄인 것."""
    code: str
    name: str | None
    side: str                   # buy / sell
    purpose: str                # entry / exit / stop / stopAmend
    quantity: int               # 체결 수량(미체결이면 0)
    price: float | None         # 평균 체결가
    status: str


class HoldingLine(NamedTuple):
    """사이클이 끝난 뒤의 보유 한 건."""
    code: str
    name: str | None
    quantity: int
    average_price: float
    stop_price: float | None


def _label(code: str, name: str | None) -> str:
    return f"{name}({code})" if name else f"`{code}`"


_PURPOSE = {"entry": "매수", "exit": "매도", "stop": "손절예약", "stopAmend": "손절정정"}


def notify_cycle_summary(
    cycle_id: str, status: str, *, action: str, watchlist: int, planned: int,
    live: bool, trades: Sequence[TradeLine] = (), holdings: Sequence[HoldingLine] = (),
    reason: str | None = None, mode: str = "real",
) -> bool:
    """정기 사이클 결과 — 무엇을 사고 팔았고 지금 무엇을 들고 있는지.

    실패는 `notify_cycle_failure`가 따로 보낸다. 같은 사이클에 알림이 두 번 가지
    않도록 호출부에서 갈라 부른다.
    """
    skipped = status != "recorded"
    out = [f"사이클: `{cycle_id}` · `{mode}`", f"결과: {status} ({action})"]
    if reason:
        out.append(f"사유: {reason}")
    if skipped:
        return send("\n".join(out), level="warning", title="사이클 건너뜀")

    out.append(f"워치리스트 {watchlist:,}종목 · 집행계획 {planned}건")

    buys = [t for t in trades if t.purpose == "entry"]
    sells = [t for t in trades if t.purpose == "exit"]
    stops = [t for t in trades if t.purpose in ("stop", "stopAmend")]

    out.append("")
    out.append(f"**매수** {len(buys)}건" if buys else "**매수** 없음")
    out += [f"· {_trade_line(t)}" for t in buys]
    out.append(f"**매도** {len(sells)}건" if sells else "**매도** 없음")
    out += [f"· {_trade_line(t)}" for t in sells]
    if stops:
        out.append(f"**손절 예약** {len(stops)}건")
        out += [f"· {_trade_line(t)}" for t in stops]

    out.append("")
    if holdings:
        total = sum(h.quantity * h.average_price for h in holdings)
        out.append(f"**보유** {len(holdings)}종목 · 매입금액 {total:,.0f}원")
        for h in holdings:
            stop = f" 손절 {h.stop_price:,.0f}" if h.stop_price else " 손절 없음"
            out.append(f"· {_label(h.code, h.name)} {h.quantity}주 "
                       f"@ {h.average_price:,.0f}{stop}")
    else:
        out.append("**보유** 없음")

    if not live:
        out.append("")
        out.append("드라이런 — 계획만 세우고 주문은 내지 않았습니다(`--live` 없음).")
    # 손절 없는 보유가 하나라도 있으면 밤사이 갭에 무방비다 — 눈에 띄게 올린다.
    naked = [h for h in holdings if h.stop_price is None]
    return send("\n".join(out),
                level="warning" if naked else "info", title="사이클 완료")


def _trade_line(t: TradeLine) -> str:
    what = _PURPOSE.get(t.purpose, t.purpose)
    if t.quantity <= 0:
        return f"{_label(t.code, t.name)} {what} 미체결(`{t.status}`)"
    at = f" @ {t.price:,.0f}원" if t.price else ""
    return f"{_label(t.code, t.name)} {what} {t.quantity}주{at}"


def notify_stop_filled(
    code: str, *, quantity: int, entry: float, exit_price: float, net: float,
    return_percent: float, name: str | None = None, mode: str = "real",
) -> bool:
    """걸어 둔 손절이 스스로 체결됐음을 알린다.

    우리가 낸 주문이 아니라 브로커가 발동시킨 매도라, 알려주지 않으면 사람은 다음
    사이클 결과를 볼 때까지 팔린 줄도 모른다.
    """
    sign = "+" if net >= 0 else "−"
    return send(
        f"{_label(code, name)} {quantity}주가 손절가에 닿아 체결됐습니다. · `{mode}`\n"
        f"매수 {entry:,.0f}원 → 매도 {exit_price:,.0f}원\n"
        f"실현손익 {sign}{abs(net):,.0f}원 ({return_percent * 100:+.2f}%)\n\n"
        "보유와 손익은 장부에 이미 반영했습니다. 따로 하실 일은 없습니다.",
        level="warning", title="손절 체결",
    )


def notify_watch_summary(
    *, positions: int, missing: int, registered: int,
    stale: Sequence[str] = (), revised: Sequence[str] = (),
    gaps: Sequence[str] = (), market_open: bool = True, mode: str = "real",
) -> bool:
    """장중 보유 감시 결과 — 트레일링(손절선 정정)이 실제로 반영됐는지가 핵심이다.

    보유가 0이면 호출부가 아예 부르지 않는다 — 30분마다 "보유 없음"이 13번 오면
    알림 자체가 배경 소음이 되어 진짜 경보를 놓친다. 안 도는 것은 heartbeat가 잡는다.
    """
    out = [f"보유 {positions}종목 감시 · `{mode}`"]
    if not market_open:
        out.append("장 마감 후 정리 — 주문은 낼 수 없어 장부만 맞췄습니다.")
    if not missing:
        out.append("① 상주 스톱: 정상")
    elif market_open:
        out.append(f"① 상주 스톱: 빠짐 {missing}종목 → 등록 {registered}건")
    else:
        out.append(f"① 상주 스톱: 빠짐 {missing}종목 — 장이 닫혀 등록하지 못했습니다")
    if stale:
        out.append(f"② 손절선 정정 {len(revised)}/{len(stale)}건")
        out += [f"· {t}" for t in stale]
        if len(revised) < len(stale):
            out.append("  일부가 정정되지 않았습니다 — 그 종목은 옛 손절가 그대로입니다.")
    else:
        out.append("② 손절선: 장부와 KIS 예약 일치(정정할 것 없음)")
    out.append("③ 손절 구멍: " + ("없음" if not gaps else ", ".join(gaps)))
    # 마감 후에 손절 없는 보유가 남아 있으면 밤사이 갭에 그대로 노출된다.
    if not market_open and missing:
        out.append("")
        out.append("**손절 없이 밤을 넘깁니다.** 다음 거래일 09:00 감시가 재등록을 "
                   "시도하지만, 갭하락은 그 전에 벌어집니다.")

    level = "critical" if gaps or (missing and not market_open) else (
        "warning" if (missing or len(revised) < len(stale)) else "info")
    return send("\n".join(out), level=level,
                title="보유 감시" + ("" if level == "info" else " — 조치 필요"))


def notify_system_health(
    readings: Sequence[tuple[str, str, str]], *, healthy: bool = False,
) -> bool:
    """서버 자원 상태 알림(10-ops 10.4·10.10).

    이상이 있을 때만 호출부가 부른다. 고칠 때까지 매일 같은 알림이 오는데, 그게
    맞다 — 디스크는 저절로 줄지 않고, 차는 순간 DB·로그·백업이 한꺼번에 멈춘다.
    """
    worst = "info"
    for _, level, _ in readings:
        if _WORST_LEVEL.index(level) > _WORST_LEVEL.index(worst):
            worst = level
    lines = [f"{_STEP_MARK_BY_LEVEL[level]} {name}: {line}" for name, level, line in readings]
    tail = "" if healthy else (
        "\n\n디스크가 차면 DB·로그·백업이 한꺼번에 멈춥니다. 오래된 백업"
        "(`/var/backups/alphaloop`)과 시스템 로그(`journalctl --vacuum-size=`)부터 줄이세요."
    )
    return send(
        "\n".join(lines) + tail,
        level=worst,
        title="서버 상태 정상" if healthy else "서버 자원 경고",
    )


def notify_stop_not_registered(code: str, qty: int, stop_price: float, status: str) -> bool:
    """손절 스톱이 걸리지 않은 채 보유가 생겼음을 알린다 — 장 마감 후 갭에 무방비인 상태다."""
    return send(
        f"종목: `{code}` {qty}주\n걸려던 손절: {stop_price:,.0f}원\n브로커 응답: `{status}`\n\n"
        "매수는 체결됐는데 손절 예약이 서지 않았습니다. 다음 감시(30분 내)가 재등록을 시도하지만, "
        "계속 실패하면 장 마감 전에 직접 손절을 걸거나 보유를 정리하세요.",
        level="critical", title="손절 미등록 보유 발생",
    )


def notify_stop_not_revised(code: str, new_stop: float, reason: str) -> bool:
    """손절선을 올리려 했는데 브로커 예약을 못 고쳤음을 알린다 — 이익 보존이 깨진 상태다."""
    return send(
        f"종목: `{code}`\n올리려던 손절: {new_stop:,.0f}원\n실패 사유: {reason}\n\n"
        "장부의 손절선만 올라가고 증권사 예약은 옛 가격 그대로입니다. "
        "파산 방지(초기 손절)는 살아 있지만 밤사이 갭에서는 옛 가격으로 체결됩니다.",
        level="warning", title="손절 정정 실패",
    )


def notify_cycle_failure(cycle_id: str, step: int | None, reason: str) -> bool:
    """사이클이 도중에 죽었을 때 어느 단계에서 멈췄는지 알린다."""
    at = f"{step}단계" if step else "단계 미상"
    return send(f"사이클: `{cycle_id}`\n멈춘 곳: {at}\n사유: {reason}",
                level="warning", title="사이클 실패")
