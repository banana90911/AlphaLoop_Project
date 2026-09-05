/**
 * description:        ④ 오류·정지 알림
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:            08-dashboard 8.4 ④
 *                     대시보드는 읽기 전용이라 해제 버튼을 누르는 곳이 아니다 —
 *                     무엇을 해야 하는지 알려주는 곳이다.
 */

import type { ReactNode } from 'react'
import { useState } from 'react'
import type { AlertsResponse, CashFlow, FailedCycle, IngestRun, SafeStopEvent } from '../api'
import { fmtDate, fmtStamp, fmtWonSigned } from '../format'
import { Badge, Empty, ErrorLine, Panel, Skeleton } from './ui'

// 건너뜀 사유는 코드 이름 그대로 오므로 화면에서 풀어 준다
const SKIP_REASON: Record<string, string> = {
  marketHalt: '시장 전체 정지 · 휴장',
  safeStop: '안전 정지 중',
  dataStale: '데이터가 낡음',
  noCandidates: '조건을 넘은 후보 없음',
  circuitBreaker: '서킷브레이커 발동',
}

const STEP_NAME: Record<number, string> = {
  1: '후보 선별',
  2: '데이터 수집',
  3: '결정',
  4: '리스크 검증',
  5: '주문 실행',
  6: '기록',
}

/** 행을 고르면 원인과 "무엇을 확인하고 어떻게 해제하는지"를 함께 보여준다(8.4 ④) */
function stopGuide(e: SafeStopEvent): { what: string; how: string; manual: boolean } {
  const cause = e.cause.toLowerCase()
  if (cause.includes('balance') || cause.includes('잔고') || cause.includes('sync'))
    return {
      what: '우리 기록의 보유 수량과 KIS 실잔고가 어긋났습니다. 어긋난 채로 주문하면 없는 주식을 팔거나 두 번 살 수 있어 전체를 멈춥니다.',
      how: 'KIS에서 실제 보유를 확인해 Positions를 맞춘 뒤 사람이 직접 해제합니다. 자동 해제는 없습니다.',
      manual: true,
    }
  if (cause.includes('data') || cause.includes('fresh') || cause.includes('신선'))
    return {
      what: '결정에 쓰는 데이터가 낡았습니다. 낡은 값으로 낸 점수는 오늘의 시장이 아닙니다.',
      how: '일일 배치를 다시 돌려 데이터를 채운 뒤 해제합니다 — python run_daily_ingest.py',
      manual: true,
    }
  if (['cash', 'withdraw', 'outflow', '유출'].some((k) => cause.includes(k)))
    return {
      what: '설명되지 않는 큰 현금 유출이 감지됐습니다. 내가 뺀 돈인지 사고인지 가리기 전에는 매매를 멈춥니다.',
      how: '증권사 앱에서 이체 내역을 확인하고, 내 이체가 맞으면 라벨을 붙인 뒤 해제합니다.',
      manual: true,
    }
  return {
    what: '자동 규칙이 매매 전체를 멈췄습니다. 신규 주문만 막히고 보유 청산은 계속 돕니다.',
    how: '원인을 확인한 뒤 사람이 직접 해제합니다.',
    manual: true,
  }
}

export function AlertPanel({
  data,
  error,
  loading,
}: {
  data: AlertsResponse | null
  error: string | null
  loading: boolean
}) {
  const stops = data?.safe_stops ?? []
  const cycles = data?.failed_cycles ?? []
  const ingests = data?.failed_ingests ?? []
  const flows = data?.unlabeled_flows ?? []
  const total = stops.length + cycles.length + ingests.length + flows.length

  return (
    <Panel
      title="오류 · 정지"
      subtitle="차단은 미수와 대형 유출 SafeStop 둘뿐. 나머지는 알려만 준다"
      right={
        data?.active_stop ? (
          <Badge tone="up">지금 정지 중</Badge>
        ) : (
          <Badge tone="buy">정상 가동</Badge>
        )
      }
    >
      {error && <ErrorLine message={error} />}

      {loading && !data ? (
        <div className="space-y-2 p-5">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : total === 0 ? (
        <Empty>정지도 실패도 없습니다.</Empty>
      ) : (
        <div className="divide-y divide-ink-800">
          {stops.length > 0 && (
            <Group title="안전 정지" count={stops.length}>
              {stops.map((e) => (
                <StopRow key={e.event_id} e={e} />
              ))}
            </Group>
          )}
          {cycles.length > 0 && (
            <Group title="실패 · 건너뛴 사이클" count={cycles.length}>
              {cycles.map((c) => (
                <CycleRow key={c.cycle_id} c={c} />
              ))}
            </Group>
          )}
          {ingests.length > 0 && (
            <Group title="일일 배치" count={ingests.length}>
              {ingests.map((r) => (
                <IngestRow key={r.run_id} r={r} />
              ))}
            </Group>
          )}
          {flows.length > 0 && (
            <Group title="미분류 현금 변동" count={flows.length} info>
              {flows.map((f) => (
                <FlowRow key={f.flow_id} f={f} hint={data?.unlabeled_flow_hint ?? ''} />
              ))}
            </Group>
          )}
        </div>
      )}
    </Panel>
  )
}

function Group({
  title,
  count,
  info,
  children,
}: {
  title: string
  count: number
  info?: boolean
  children: ReactNode
}) {
  return (
    <div className="px-5 py-4">
      <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold text-ink-200">
        {title}
        <span className="font-mono text-[11px] text-ink-400">{count}</span>
        {info && <Badge tone="neutral">정보성 · 매매를 막지 않음</Badge>}
      </h3>
      <ul className="space-y-1.5">{children}</ul>
    </div>
  )
}

function Row({
  head,
  meta,
  detail,
  tone = 'border-ink-800',
}: {
  head: ReactNode
  meta: ReactNode
  detail: ReactNode
  tone?: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <li className={`rounded-lg border bg-ink-850/50 ${tone}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-3 py-2 text-left"
      >
        <div className="min-w-0 flex-1 text-xs">{head}</div>
        <div className="font-mono text-[11px] whitespace-nowrap text-ink-400">{meta}</div>
        <span className="text-[10px] text-ink-400">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="border-t border-ink-800 px-3 py-2.5 text-[11px] leading-relaxed text-ink-400">
          {detail}
        </div>
      )}
    </li>
  )
}

function StopRow({ e }: { e: SafeStopEvent }) {
  const active = e.released_date_time === null
  const g = stopGuide(e)
  return (
    <Row
      tone={active ? 'border-up/40' : 'border-ink-800'}
      head={
        <span className="flex items-center gap-2">
          <Badge tone={active ? 'up' : 'neutral'}>{active ? '정지 중' : '해제됨'}</Badge>
          <span className="truncate text-ink-50">{e.cause}</span>
          <span className="text-ink-400">{e.trigger === 'auto' ? '자동' : '수동'}</span>
        </span>
      }
      meta={fmtStamp(e.occurred_date_time)}
      detail={
        <>
          <p className="mb-1.5 text-ink-200">{g.what}</p>
          <p className="mb-1.5">{g.how}</p>
          <div className="mt-2 grid gap-1 sm:grid-cols-2">
            <span>사이클 {e.cycle_id ?? '—'}</span>
            <span>
              해제 {e.released_date_time ? `${fmtStamp(e.released_date_time)} · ${e.released_by ?? ''}` : '—'}
            </span>
            {e.release_reason && <span className="sm:col-span-2">사유: {e.release_reason}</span>}
          </div>
        </>
      }
    />
  )
}

function CycleRow({ c }: { c: FailedCycle }) {
  const failed = c.status === 'failed'
  return (
    <Row
      head={
        <span className="flex items-center gap-2">
          <Badge tone={failed ? 'warn' : 'neutral'}>{failed ? '실패' : '건너뜀'}</Badge>
          <span className="text-ink-50">
            {failed
              ? `${c.failed_step ?? '?'}단계 ${c.failed_step ? (STEP_NAME[c.failed_step] ?? '') : ''}`
              : c.skip_reason
                ? (SKIP_REASON[c.skip_reason] ?? c.skip_reason)
                : '사유 없음'}
          </span>
        </span>
      }
      meta={`${fmtDate(c.trade_date)} ${fmtStamp(c.started_date_time).slice(-5)}`}
      detail={
        <>
          <p className="mb-1.5 text-ink-200">
            {failed
              ? '사이클이 중간에 멈췄습니다. 그 단계 이후의 판단·주문은 일어나지 않았습니다.'
              : '사이클이 조건에 따라 통째로 건너뛰어졌습니다(휴장·정지 등).'}
          </p>
          <p>
            {failed
              ? '로그에서 해당 CycleId를 찾아 원인을 확인합니다. 5단계(주문 실행)에서 멈췄다면 KIS 주문 조회로 실제 송출 여부를 먼저 확인해야 중복 주문을 피합니다.'
              : '정상 동작일 수 있습니다. 사유를 확인하세요.'}
          </p>
          <p className="mt-2 font-mono">CycleId {c.cycle_id}</p>
        </>
      }
    />
  )
}

function IngestRow({ r }: { r: IngestRun }) {
  const failed = r.status === 'failed'
  return (
    <Row
      tone={failed ? 'border-warn/40' : 'border-ink-800'}
      head={
        <span className="flex items-center gap-2">
          <Badge tone="warn">{failed ? '실패' : '부분 성공'}</Badge>
          <span className="text-ink-50">{r.target_table}</span>
          <span className="text-ink-400">{r.source}</span>
        </span>
      }
      meta={`${r.success_count ?? 0}/${r.target_count ?? 0}`}
      detail={
        <>
          <p className="mb-1.5 text-ink-200">
            그날 데이터가 낡았다는 뜻입니다. 낡은 값으로 낸 점수는 오늘의 시장이 아니므로,
            사이클이 신선도 검사에서 스스로 멈출 수 있습니다.
          </p>
          <p>배치를 다시 돌립니다 — 이어받기가 되므로 성공한 종목은 다시 받지 않습니다.</p>
          <code className="mt-2 block rounded border border-ink-800 bg-ink-950 px-2 py-1.5 font-mono">
            python run_daily_ingest.py
          </code>
          {r.error_message && (
            <p className="mt-2 break-all text-ink-200">오류: {r.error_message}</p>
          )}
          <p className="mt-1 font-mono">
            {fmtStamp(r.started_date_time)} → {fmtStamp(r.finished_date_time)}
          </p>
        </>
      }
    />
  )
}

function FlowRow({ f, hint }: { f: CashFlow; hint: string }) {
  const inbound = Number(f.amount) >= 0
  return (
    <Row
      head={
        <span className="flex items-center gap-2">
          <Badge tone="neutral">미분류</Badge>
          <span className={`font-mono ${inbound ? 'text-up' : 'text-down'}`}>
            {fmtWonSigned(f.amount)}원
          </span>
          <span className="text-ink-400">{f.source}</span>
        </span>
      }
      meta={fmtDate(f.trade_date)}
      detail={
        <>
          <p className="mb-1.5 text-ink-200">
            매매로 설명되지 않는 현금 변동을 기록해 뒀습니다. 라벨이 아직 안 붙었다는
            안내일 뿐, 매매를 막고 있지 않습니다.
          </p>
          <p>
            입금·출금이면 TWR에서 빼야 하고, 배당·이자·세금 환급이면 수익이라 빼면 안 됩니다.
            그래서 라벨이 필요합니다.
          </p>
          <code className="mt-2 block rounded border border-ink-800 bg-ink-950 px-2 py-1.5 font-mono break-all">
            {hint.replace('<FlowId>', f.flow_id)}
          </code>
          <p className="mt-2 font-mono">
            기대 {fmtWonSigned(f.expected_cash)} / 실제 {fmtWonSigned(f.actual_cash)}
          </p>
        </>
      }
    />
  )
}
