/**
 * description:        ③ 거래 리포트 — 목록 + 근거 펼침
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:            08-dashboard 8.4 ③
 *                     주문과 입출금을 한 줄씩 시간순으로 섞는다. 매매와 이체가 따로 놀면
 *                     "이 구간에 왜 돈이 늘었나"를 사람이 못 잇기 때문이다.
 */

import { useEffect, useState } from 'react'
import type { CashFlow, Order, TradeDetail, TradesResponse } from '../api'
import { getTradeDetail } from '../api'
import {
  fmtDate,
  fmtNumber,
  fmtPercent,
  fmtPercentile,
  fmtStamp,
  fmtTime,
  fmtWon,
  fmtWonSigned,
} from '../format'
import { Badge, Empty, ErrorLine, Field, FieldGroup, Panel, Skeleton, Toggle } from './ui'

export type SideFilter = 'all' | 'buy' | 'sell' | 'flow'

export const SIDE_OPTIONS: { value: SideFilter; label: string }[] = [
  { value: 'all', label: '전체' },
  { value: 'buy', label: '매수' },
  { value: 'sell', label: '매도' },
  { value: 'flow', label: '입출금' },
]

const FLOW_KIND: Record<string, string> = {
  deposit: '입금',
  withdrawal: '출금',
  dividend: '배당',
  taxRefund: '세금 환급',
  interest: '이자',
  fee: '수수료',
  unknown: '미분류',
}

const FLOW_STATUS: Record<string, string> = {
  unconfirmed: '미확인',
  confirmed: '확인됨',
  reclassified: '재분류',
}

const ORDER_STATUS: Record<string, string> = {
  submitted: '접수',
  partial: '부분체결',
  filled: '체결',
  cancelled: '취소',
  rejected: '거부',
}

const EXIT_REASON: Record<string, string> = {
  breakeven: '본전',
  stopHit: '손절',
  timeExit: '시간 청산',
  thesisInvalid: '근거 소멸',
  trail: '트레일링',
}

const CHECK_NAME: Record<string, string> = {
  balanceSync: '잔고 정합성',
  cashFlow: '외부 현금흐름',
  marketHalt: '시장 상태',
  dataFreshness: '데이터 신선도',
  circuitBreaker: '서킷브레이커',
  schema: '스키마',
  hardLimit: '하드룰 한도',
  symbolState: '종목 상태',
}

const CHECK_RESULT: Record<string, { label: string; tone: 'neutral' | 'up' | 'warn' | 'buy' }> = {
  pass: { label: '통과', tone: 'buy' },
  reject: { label: '거부', tone: 'up' },
  reduce: { label: '축소', tone: 'warn' },
  skipCycle: { label: '사이클 건너뜀', tone: 'warn' },
  safeStop: { label: '안전 정지', tone: 'up' },
  flowDetected: { label: '흐름 기록', tone: 'neutral' },
}

const DECISION_REASON: Record<string, string> = {
  entryThreshold: '진입 임계 통과',
  thesisInvalid: '근거 소멸',
  stopHit: '손절 도달',
  timeExit: '시간 청산',
  breakeven: '본전 상향',
  trail: '트레일링 상향',
  costExceedsEdge: '비용이 엣지를 넘음',
}

/** 주문과 입출금을 한 종류의 행으로 접어 시간순으로 합친다 */
type Row =
  | { key: string; at: number; kind: 'order'; order: Order }
  | { key: string; at: number; kind: 'flow'; flow: CashFlow }

function buildRows(data: TradesResponse | null): Row[] {
  if (!data) return []
  const rows: Row[] = [
    ...data.orders.map((o) => ({
      key: `o:${o.client_order_id}`,
      at: Date.parse(o.filled_date_time ?? o.ordered_date_time),
      kind: 'order' as const,
      order: o,
    })),
    ...data.flows.map((f) => ({
      key: `f:${f.flow_id}`,
      at: Date.parse(f.detected_date_time),
      kind: 'flow' as const,
      flow: f,
    })),
  ]
  return rows.sort((a, b) => b.at - a.at)
}

export function TradeReport({
  data,
  error,
  loading,
  side,
  onSideChange,
  start,
  end,
  onRangeChange,
}: {
  data: TradesResponse | null
  error: string | null
  loading: boolean
  side: SideFilter
  onSideChange: (s: SideFilter) => void
  start: string
  end: string
  onRangeChange: (r: { start: string; end: string }) => void
}) {
  const [openKey, setOpenKey] = useState<string | null>(null)
  const rows = buildRows(data)

  return (
    <Panel
      title="거래 리포트"
      subtitle="매매와 입출금을 한 줄씩 시간순으로"
      right={<Toggle options={SIDE_OPTIONS} value={side} onChange={onSideChange} size="sm" />}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-800 px-5 py-3 text-xs text-ink-400">
        <label className="flex items-center gap-1.5">
          기간
          <input
            type="date"
            value={start}
            max={end || undefined}
            onChange={(e) => onRangeChange({ start: e.target.value, end })}
            className="rounded-md border border-ink-800 bg-ink-950 px-2 py-1 font-mono text-[11px] text-ink-50 outline-none focus:border-ink-700"
          />
        </label>
        <span>—</span>
        <input
          type="date"
          value={end}
          min={start || undefined}
          onChange={(e) => onRangeChange({ start, end: e.target.value })}
          className="rounded-md border border-ink-800 bg-ink-950 px-2 py-1 font-mono text-[11px] text-ink-50 outline-none focus:border-ink-700"
        />
        {(start || end) && (
          <button
            type="button"
            onClick={() => onRangeChange({ start: '', end: '' })}
            className="rounded-md border border-ink-800 px-2 py-1 text-[11px] hover:bg-ink-850 hover:text-ink-50"
          >
            기간 해제
          </button>
        )}
        <span className="ml-auto font-mono text-[11px]">{rows.length}건</span>
      </div>

      {error && <ErrorLine message={error} />}

      {loading && !data ? (
        <div className="space-y-2 p-5">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <Empty>이 조건에 맞는 거래가 없습니다.</Empty>
      ) : (
        <div className="max-h-[560px] overflow-auto">
          <table className="w-full min-w-[880px] text-sm">
            <thead className="sticky top-0 z-10 bg-ink-900">
              <tr className="border-b border-ink-800 text-left text-[11px] font-medium text-ink-400">
                <th className="py-2 pr-3 pl-5 font-medium">날짜</th>
                <th className="px-3 py-2 font-medium">시각</th>
                <th className="px-3 py-2 font-medium">구분</th>
                <th className="px-3 py-2 font-medium">종목</th>
                <th className="px-3 py-2 text-right font-medium">단가</th>
                <th className="px-3 py-2 text-right font-medium">수량</th>
                <th className="px-3 py-2 text-right font-medium">거래대금</th>
                <th className="px-3 py-2 text-right font-medium">손절가</th>
                <th className="px-3 py-2 font-medium">상태</th>
                <th className="w-8 py-2 pr-5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800/70">
              {rows.map((r) =>
                r.kind === 'order' ? (
                  <OrderRow
                    key={r.key}
                    order={r.order}
                    open={openKey === r.key}
                    onToggle={() => setOpenKey(openKey === r.key ? null : r.key)}
                  />
                ) : (
                  <FlowRow
                    key={r.key}
                    flow={r.flow}
                    open={openKey === r.key}
                    onToggle={() => setOpenKey(openKey === r.key ? null : r.key)}
                  />
                ),
              )}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

const CELL = 'px-3 py-2.5 font-mono text-xs whitespace-nowrap'

function OrderRow({
  order,
  open,
  onToggle,
}: {
  order: Order
  open: boolean
  onToggle: () => void
}) {
  const buy = order.side === 'buy'
  const price = order.average_fill_price ?? order.order_price
  const qty = order.filled_quantity || order.order_quantity
  // 거래대금은 현금이 어느 쪽으로 움직였는지를 부호로 읽힌다 — 매수 −, 매도 +
  const cash = price === null ? null : (buy ? -1 : 1) * price * qty
  const at = order.filled_date_time ?? order.ordered_date_time
  const filled = order.status === 'filled' || order.status === 'partial'

  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer transition-colors hover:bg-ink-850/60 ${open ? 'bg-ink-850/60' : ''}`}
      >
        <td className={`${CELL} pl-5 text-ink-200`}>{fmtDate(at)}</td>
        <td className={`${CELL} text-ink-400`}>{fmtTime(at)}</td>
        <td className={CELL}>
          <Badge tone={buy ? 'buy' : 'sell'}>{buy ? '매수' : '매도'}</Badge>
        </td>
        <td className={`${CELL} font-sans`}>
          <span className="text-ink-50">{order.name ?? order.symbol_id}</span>{' '}
          <span className="font-mono text-[11px] text-ink-400">{order.symbol_id}</span>
        </td>
        <td className={`${CELL} text-right`}>{fmtWon(price)}</td>
        <td className={`${CELL} text-right`}>{qty.toLocaleString('ko-KR')}</td>
        <td className={`${CELL} text-right font-semibold ${buy ? 'text-buy' : 'text-sell'}`}>
          {fmtWonSigned(cash)}
        </td>
        <td className={`${CELL} text-right text-ink-400`}>
          {fmtWon(order.trigger_price ?? order.stop_price)}
        </td>
        <td className={CELL}>
          {!filled ? (
            <Badge tone="warn">{ORDER_STATUS[order.status] ?? order.status}</Badge>
          ) : order.position_status === 'open' ? (
            <Badge tone="neutral">보유</Badge>
          ) : order.position_status === 'frozen' ? (
            <Badge tone="warn">동결</Badge>
          ) : (
            <Badge tone="neutral">청산</Badge>
          )}
        </td>
        <td className="w-8 pr-5 text-right text-ink-400">{open ? '▲' : '▼'}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={10} className="bg-ink-950/60 px-5 py-4">
            <OrderDetail clientOrderId={order.client_order_id} />
          </td>
        </tr>
      )}
    </>
  )
}

function FlowRow({
  flow,
  open,
  onToggle,
}: {
  flow: CashFlow
  open: boolean
  onToggle: () => void
}) {
  const inbound = Number(flow.amount) >= 0
  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer transition-colors hover:bg-ink-850/60 ${open ? 'bg-ink-850/60' : ''}`}
      >
        <td className={`${CELL} pl-5 text-ink-200`}>{fmtDate(flow.trade_date)}</td>
        <td className={`${CELL} text-ink-400`}>{fmtTime(flow.detected_date_time)}</td>
        <td className={CELL}>
          <Badge tone="flow">{FLOW_KIND[flow.kind] ?? flow.kind}</Badge>
        </td>
        <td className={`${CELL} font-sans text-ink-400`}>계좌 이체</td>
        <td className={`${CELL} text-right text-ink-700`}>—</td>
        <td className={`${CELL} text-right text-ink-700`}>—</td>
        {/* 부호 규칙을 매매와 그대로 잇는다 — 거래대금이 곧 현금 방향(8.4 ③) */}
        <td className={`${CELL} text-right font-semibold ${inbound ? 'text-sell' : 'text-buy'}`}>
          {fmtWonSigned(flow.amount)}
        </td>
        <td className={`${CELL} text-right text-ink-700`}>—</td>
        <td className={CELL}>
          <Badge tone={flow.status === 'unconfirmed' ? 'warn' : 'neutral'}>
            {FLOW_STATUS[flow.status] ?? flow.status}
          </Badge>
        </td>
        <td className="w-8 pr-5 text-right text-ink-400">{open ? '▲' : '▼'}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={10} className="bg-ink-950/60 px-5 py-4">
            <div className="grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
              <FieldGroup title="감지 근거">
                <Field label="기대 예수금">{fmtWon(flow.expected_cash)}원</Field>
                <Field label="실제 예수금">{fmtWon(flow.actual_cash)}원</Field>
                <Field label="잔차">
                  {fmtWonSigned(Number(flow.actual_cash) - Number(flow.expected_cash))}원
                </Field>
                <Field label="감지 경로">{flow.source}</Field>
              </FieldGroup>
              <FieldGroup title="확인 상태">
                <Field label="상태">{FLOW_STATUS[flow.status] ?? flow.status}</Field>
                <Field label="감지 사이클">{flow.detected_cycle_id ?? '—'}</Field>
                <Field label="감지 시각">{fmtStamp(flow.detected_date_time)}</Field>
                <Field label="확인 시각">{fmtStamp(flow.confirmed_date_time)}</Field>
              </FieldGroup>
              {flow.status === 'unconfirmed' && (
                <div className="min-w-0">
                  <h4 className="mb-1 text-[11px] font-semibold tracking-wide text-ink-200 uppercase">
                    라벨 붙이기
                  </h4>
                  <p className="mb-2 text-[11px] leading-relaxed text-ink-400">
                    대시보드는 읽기 전용이라 여기서 라벨을 붙일 수 없습니다. 터미널에서:
                  </p>
                  <code className="block rounded-lg border border-ink-800 bg-ink-950 px-2.5 py-2 font-mono text-[11px] break-all text-ink-200">
                    python -m ops.cashflow confirm --id {flow.flow_id} --kind deposit
                  </code>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

/** 펼침 — 진입 근거 · 게이트 결과 · 청산 결과. 열 때 한 번만 불러온다. */
function OrderDetail({ clientOrderId }: { clientOrderId: string }) {
  const [detail, setDetail] = useState<TradeDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setDetail(null)
    setErr(null)
    getTradeDetail(clientOrderId)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : '조회 실패'))
    return () => {
      alive = false
    }
  }, [clientOrderId])

  if (err) return <ErrorLine message={err} />
  if (!detail) return <Skeleton className="h-24 w-full" />

  const { decision: d, cycle_score: cs, risk_checks: checks, outcome: out, order } = detail
  const isEntry = order.purpose === 'entry'

  return (
    <div className="grid gap-x-8 gap-y-4 md:grid-cols-2 lg:grid-cols-4">
      {/* 같은 표를 매수·매도가 함께 쓰지만 결정의 성격이 다르다 —
          매수는 "왜 샀나"(점수·사이징), 매도는 "왜 팔았나"(무효화·손절선)다.
          매도 행에 사이징 항목을 남겨 두면 전부 '—'이라 읽는 사람이 헤맨다. */}
      <FieldGroup title={isEntry ? '진입 근거' : '청산 판단'}>
        {d ? (
          <>
            <Field label="판정">{DECISION_REASON[d.reason] ?? d.reason}</Field>
            {isEntry && (
              <Field label="점수 / 임계">
                {fmtNumber(d.score, 3)} / {fmtNumber(d.threshold, 3)}
              </Field>
            )}
            <Field label={isEntry ? '진입가' : '기준가'}>{fmtWon(d.entry_price)}원</Field>
            <Field label="손절가">{fmtWon(d.stop_price)}원</Field>
            <Field label="R (주당 위험)">{fmtWon(d.risk_per_share)}원</Field>
            {isEntry && (
              <>
                <Field label="목표 보유 수">{d.target_positions ?? '—'}종목</Field>
                <Field label="수량">{d.quantity ?? '—'}주</Field>
                <Field label="기대 비용 / 엣지">
                  {fmtWon(d.estimated_cost)} / {fmtWonSigned(d.net_edge)}
                </Field>
              </>
            )}
            <Field label="판단 시각">{fmtStamp(d.decided_date_time)}</Field>
          </>
        ) : (
          <p className="text-[11px] text-ink-400">
            결정 기록이 없는 주문입니다(상주 스톱 자동 체결 등).
          </p>
        )}
      </FieldGroup>

      <FieldGroup title="점수 항목">
        {cs ? (
          <>
            <Field label="편입 사유">{cs.inclusion}</Field>
            <Field label="종합 점수">{fmtNumber(cs.total_score, 3)}</Field>
            <Field label="전일 기준 점수">{fmtNumber(cs.base_score, 3)}</Field>
            <Field label="장중 수급 백분위">{fmtPercentile(cs.flow_percentile_live)}</Field>
            <Field label="ATR / 손절폭">
              {fmtWon(cs.atr)} / {fmtWon(cs.stop_width)}
            </Field>
            <Field label="레짐">{d?.regime ?? '—'}</Field>
            <Field label="거래 가능">
              {cs.is_tradable === false ? (cs.block_reason ?? '불가') : '가능'}
            </Field>
          </>
        ) : (
          <p className="text-[11px] text-ink-400">사이클 점수 기록이 없습니다.</p>
        )}
      </FieldGroup>

      <FieldGroup title="게이트 결과">
        {checks.length > 0 ? (
          <ul className="space-y-1">
            {checks.map((c) => {
              const meta = CHECK_RESULT[c.result] ?? { label: c.result, tone: 'neutral' as const }
              return (
                <li
                  key={c.check_id}
                  className="flex items-baseline justify-between gap-2 border-b border-ink-800/60 py-1.5 last:border-0"
                >
                  <span className="text-[11px] text-ink-400">
                    <span className="mr-1.5 font-mono text-ink-700">{c.check_order}</span>
                    {CHECK_NAME[c.check_name] ?? c.check_name}
                  </span>
                  <span className="flex items-center gap-1.5 text-right">
                    {c.actual_value !== null && (
                      <span className="font-mono text-[11px] text-ink-400">
                        {fmtNumber(c.actual_value, 2)}
                        {c.limit_value !== null && ` / ${fmtNumber(c.limit_value, 2)}`}
                      </span>
                    )}
                    <Badge tone={meta.tone}>{meta.label}</Badge>
                  </span>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="text-[11px] text-ink-400">게이트 기록이 없습니다.</p>
        )}
      </FieldGroup>

      <FieldGroup title={isEntry ? '청산 결과' : '체결 결과'}>
        {out ? (
          <>
            <Field label="실현손익">
              <span className={Number(out.net_profit_loss) >= 0 ? 'text-up' : 'text-down'}>
                {fmtWonSigned(out.net_profit_loss)}원
              </span>
            </Field>
            <Field label="수익률">{fmtPercent(out.return_percent)}</Field>
            <Field label="R 배수">{fmtNumber(out.r_multiple, 2)}</Field>
            <Field label="보유일수">{out.holding_days ?? '—'}일</Field>
            <Field label="진입 → 청산">
              {fmtWon(out.entry_price)} → {fmtWon(out.exit_price)}
            </Field>
            <Field label="수수료 / 세금">
              {fmtWon(out.fee)} / {fmtWon(out.tax)}
            </Field>
            <Field label="청산 사유">
              {out.exit_reason ? (EXIT_REASON[out.exit_reason] ?? out.exit_reason) : '—'}
            </Field>
            <Field label="청산 시각">{fmtStamp(out.closed_date_time)}</Field>
          </>
        ) : (
          <>
            <p className="mb-2 text-[11px] text-ink-400">아직 청산되지 않았습니다.</p>
            <Field label="주문 상태">{ORDER_STATUS[order.status] ?? order.status}</Field>
            <Field label="체결">
              {order.filled_quantity}/{order.order_quantity}주
            </Field>
            <Field label="수수료 / 세금">
              {fmtWon(order.fee)} / {fmtWon(order.tax)}
            </Field>
          </>
        )}
      </FieldGroup>
    </div>
  )
}
