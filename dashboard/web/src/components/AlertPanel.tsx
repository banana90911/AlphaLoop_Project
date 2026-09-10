/**
 * description:        ④ 오류·정지 알림
 * author:             siheon jung
 * created date:       2026/09/04
 * last modified date: 2026/09/08
 * remarks:            08-dashboard 8.4 ④
 *                     대시보드는 읽기 전용이라 해제 버튼을 누르는 곳이 아니다 —
 *                     무엇을 해야 하는지 알려주는 곳이다.
 */

import type { ReactNode } from 'react'
import { Children, useState } from 'react'
import type {
  AlertsResponse,
  CashFlow,
  FailedCycle,
  IngestRun,
  SafeStopEvent,
  WatchRun,
} from '../api'
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

// 배치 상태 → 화면 표시. `ok`는 "대상을 전부 조회했다"는 뜻이지 "오류가 하나도
// 없었다"는 뜻은 아니다(상장폐지 종목처럼 받을 봉이 없는 경우도 성공으로 센다 — 10-ops 10.3).
const INGEST_LABEL: Record<string, string> = {
  ok: '성공',
  partial: '부분 성공',
  failed: '실패',
}
const INGEST_TONE: Record<string, 'buy' | 'warn' | 'neutral'> = {
  ok: 'buy',
  partial: 'warn',
  failed: 'warn',
}

/** 행을 고르면 원인과 "무엇을 확인하고 어떻게 해제하는지"를 함께 보여준다(8.4 ④)
 *
 * 코드가 만드는 원인 문자열은 네 개뿐이다(risk/risk_engine.py) — 그 문자열을 그대로
 * 맞춰 본다. 예전에는 '신선'으로 찾느라 "시세 데이터 이상"을 못 알아보고 뭉뚱그린
 * 문구를 내보냈다(2026-09-10). 새 원인이 생기면 여기에 한 줄을 더한다.
 */
function stopGuide(e: SafeStopEvent): { what: string; how: string } {
  const cause = e.cause
  if (cause.includes('보유 불일치') || cause.includes('balance'))
    return {
      what: '우리 기록의 보유 수량과 KIS 실잔고가 어긋났습니다. 어긋난 채로 주문하면 없는 주식을 팔거나 두 번 살 수 있어 전체를 멈춥니다.',
      how: 'KIS에서 실제 보유를 확인해 Positions를 맞춘 뒤 사람이 직접 해제합니다. 자동 해제는 없습니다.',
    }
  if (cause.includes('미수') || cause.includes('예수금 음수'))
    return {
      what: '예수금이 마이너스입니다. 살 돈이 없는데 주문이 나간 상태라 즉시 멈춥니다.',
      how: '증권사 앱에서 미수 금액을 확인해 입금하거나 보유를 정리한 뒤 해제합니다.',
    }
  if (cause.includes('현금 유출') || cause.includes('outflow'))
    return {
      what: '설명되지 않는 큰 현금 유출이 감지됐습니다. 내가 뺀 돈인지 사고인지 가리기 전에는 매매를 멈춥니다.',
      how: '증권사 앱에서 이체 내역을 확인하고, 내 이체가 맞으면 라벨을 붙인 뒤 해제합니다.',
    }
  if (cause.includes('시세 데이터') || cause.includes('데이터 이상'))
    return {
      what: '오늘 일일 배치가 온전히 끝나지 않아, 결정에 쓸 데이터가 낡았습니다. 낡은 값으로 낸 점수는 오늘의 시장이 아닙니다. 위 "일일 배치"에서 어느 단계가 실패했는지 볼 수 있습니다.',
      how: '실패한 종목만 다시 받은 뒤 해제합니다 — python run_daily_ingest.py --resume',
    }
  return {
    what: '자동 규칙이 매매 전체를 멈췄습니다. 신규 주문만 막히고 보유 청산은 계속 돕니다.',
    how: '원인을 확인한 뒤 사람이 직접 해제합니다.',
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
  const ingests = data?.ingests ?? []
  const watches = data?.watches ?? []
  const flows = data?.unlabeled_flows ?? []
  // 배치는 성공까지 함께 온다 — 문제 건수에는 성공을 세지 않는다.
  const badIngests = ingests.filter((r) => r.status !== 'ok')
  const problems = stops.length + cycles.length + badIngests.length + flows.length
  // 가장 최근 배치가 어느 거래일에 대해 어떤 결과였는지를 머리에 한 줄로 요약한다.
  // 실행 시각이 아니라 대상 거래일로 묶는다 — 08:00 배치는 UTC로는 전날이라 어긋난다.
  const lastDay = ingests[0]?.range_end_date ?? null
  const lastDayRuns = lastDay ? ingests.filter((r) => r.range_end_date === lastDay) : []
  const lastDayWorst = lastDayRuns.some((r) => r.status === 'failed')
    ? 'failed'
    : lastDayRuns.some((r) => r.status === 'partial')
      ? 'partial'
      : 'ok'

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
      ) : (
        <div className="divide-y divide-ink-800">
          {problems === 0 && <Empty>정지도 실패도 없습니다.</Empty>}
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
          {flows.length > 0 && (
            <Group title="미분류 현금 변동" count={flows.length} info>
              {flows.map((f) => (
                <FlowRow key={f.flow_id} f={f} hint={data?.unlabeled_flow_hint ?? ''} />
              ))}
            </Group>
          )}
          {/* 배치는 성공도 보여준다 — "잘 돌았다"와 "아예 안 돌았다"를 화면에서 갈라야 한다 */}
          <Group
            title="일일 배치"
            count={ingests.length}
            note={
              lastDay ? (
                <span className="flex items-center gap-1.5">
                  <Badge tone={INGEST_TONE[lastDayWorst]}>{INGEST_LABEL[lastDayWorst]}</Badge>
                  <span className="font-normal text-ink-400">
                    최근 {fmtDate(lastDay)} · {lastDayRuns.length}단계
                  </span>
                </span>
              ) : undefined
            }
          >
            {ingests.length === 0 ? (
              <li className="px-1 py-2 text-xs text-ink-400">
                배치 기록이 없습니다 — 아직 한 번도 돌지 않았거나 DB가 비어 있습니다.
              </li>
            ) : (
              ingests.map((r) => <IngestRow key={r.run_id} r={r} />)
            )}
          </Group>
          {/* 감시는 조치할 게 있을 때만 다른 표에 흔적을 남긴다 — 돌았다는 사실 자체를 보여준다 */}
          <Group
            title="장중 감시"
            count={watches.length}
            note={
              watches[0] ? (
                <span className="font-normal text-ink-400">
                  최근 {fmtStamp(watches[0].ran_date_time)}
                </span>
              ) : undefined
            }
          >
            {watches.length === 0 ? (
              <li className="px-1 py-2 text-xs text-ink-400">
                감시 기록이 없습니다 — 30분마다 도는 장중 감시가 아직 한 번도 돌지 않았습니다.
              </li>
            ) : (
              watches.map((w) => <WatchRow key={w.run_id} w={w} />)
            )}
          </Group>
        </div>
      )}
    </Panel>
  )
}

// 한 화면에 보여줄 줄 수. 이보다 많으면 아래에 쪽번호가 붙는다.
const PAGE_SIZE = 10

function Group({
  title,
  count,
  info,
  note,
  children,
}: {
  title: string
  count: number
  info?: boolean
  note?: ReactNode
  children: ReactNode
}) {
  const [page, setPage] = useState(0)
  const items = Children.toArray(children)
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))
  // 60초마다 다시 조회하므로 줄 수가 줄어들 수 있다 — 범위를 벗어난 쪽에 머물지 않게 자른다
  const current = Math.min(page, pages - 1)
  const shown = pages > 1 ? items.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE) : items

  return (
    <div className="px-5 py-4">
      <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold text-ink-200">
        {title}
        <span className="font-mono text-[11px] text-ink-400">{count}</span>
        {info && <Badge tone="neutral">정보성 · 매매를 막지 않음</Badge>}
        {note}
      </h3>
      <ul className="space-y-1.5">{shown}</ul>
      {pages > 1 && <Pager page={current} pages={pages} onChange={setPage} />}
    </div>
  )
}

/** 쪽번호. 쪽이 많아지면 현재 쪽 주변만 보여주고 양끝은 항상 남긴다. */
function Pager({
  page,
  pages,
  onChange,
}: {
  page: number
  pages: number
  onChange: (p: number) => void
}) {
  const nums: (number | 'gap')[] = []
  for (let i = 0; i < pages; i++) {
    if (i === 0 || i === pages - 1 || Math.abs(i - page) <= 1) nums.push(i)
    else if (nums[nums.length - 1] !== 'gap') nums.push('gap')
  }

  const btn =
    'min-w-[1.75rem] rounded-md border px-1.5 py-1 font-mono text-[11px] transition-colors'
  return (
    <div className="mt-3 flex items-center justify-center gap-1">
      <button
        type="button"
        onClick={() => onChange(page - 1)}
        disabled={page === 0}
        className={`${btn} border-ink-800 text-ink-400 enabled:hover:bg-ink-850 enabled:hover:text-ink-50 disabled:opacity-30`}
        aria-label="이전 쪽"
      >
        ‹
      </button>
      {nums.map((n, i) =>
        n === 'gap' ? (
          <span key={`gap${i}`} className="px-1 text-[11px] text-ink-700">
            …
          </span>
        ) : (
          <button
            key={n}
            type="button"
            onClick={() => onChange(n)}
            aria-current={n === page ? 'page' : undefined}
            className={
              n === page
                ? `${btn} border-ink-700 bg-ink-850 text-ink-50`
                : `${btn} border-ink-800 text-ink-400 hover:bg-ink-850 hover:text-ink-50`
            }
          >
            {n + 1}
          </button>
        ),
      )}
      <button
        type="button"
        onClick={() => onChange(page + 1)}
        disabled={page >= pages - 1}
        className={`${btn} border-ink-800 text-ink-400 enabled:hover:bg-ink-850 enabled:hover:text-ink-50 disabled:opacity-30`}
        aria-label="다음 쪽"
      >
        ›
      </button>
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
          {c.skip_reason && (
            <p className="mb-1 text-ink-200">
              {SKIP_REASON[c.skip_reason] ?? c.skip_reason}
            </p>
          )}
          <p className="font-mono">CycleId {c.cycle_id}</p>
        </>
      }
    />
  )
}

function IngestRow({ r }: { r: IngestRun }) {
  return (
    <Row
      tone={r.status === 'failed' ? 'border-warn/40' : 'border-ink-800'}
      head={
        <span className="flex items-center gap-2">
          <Badge tone={INGEST_TONE[r.status]}>{INGEST_LABEL[r.status]}</Badge>
          <span className="text-ink-50">{r.target_table}</span>
          <span className="text-ink-400">{r.source}</span>
        </span>
      }
      meta={
        r.target_count
          ? `${r.success_count ?? 0}/${r.target_count}`
          : `${(r.rows_written ?? 0).toLocaleString()}행`
      }
      detail={
        <>
          <p className="font-mono">
            {fmtStamp(r.started_date_time)} → {fmtStamp(r.finished_date_time)}
          </p>
          <p className="mt-1">적재 {(r.rows_written ?? 0).toLocaleString()}행</p>
          {r.error_message && (
            <p className="mt-1 break-all text-warn">{r.error_message}</p>
          )}
        </>
      }
    />
  )
}

function WatchRow({ w }: { w: WatchRun }) {
  // 손절 구멍은 손절선을 이탈했는데 아직 들고 있다는 뜻이라 가장 급하다.
  const bad = w.stop_gaps > 0 || w.missing_stops > w.registered_stops
  const acted = w.filled_stops + w.registered_stops + w.revised_stops
  return (
    <Row
      tone={bad ? 'border-warn/40' : 'border-ink-800'}
      head={
        <span className="flex items-center gap-2">
          <Badge tone={bad ? 'warn' : acted > 0 ? 'flow' : 'buy'}>
            {bad ? '조치 필요' : acted > 0 ? '조치함' : '이상 없음'}
          </Badge>
          <span className="text-ink-50">보유 {w.positions}종목</span>
          {!w.market_open && <span className="text-ink-400">마감 정리</span>}
        </span>
      }
      meta={fmtStamp(w.ran_date_time)}
      detail={
        <>
          <p>
            손절 체결 {w.filled_stops} · 스톱 빠짐 {w.missing_stops}(등록 {w.registered_stops})
            {' · '}손절선 어긋남 {w.stale_stops}(정정 {w.revised_stops}) · 손절 구멍{' '}
            {w.stop_gaps}
          </p>
          {w.note && <p className="mt-1 text-ink-200">{w.note}</p>}
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
