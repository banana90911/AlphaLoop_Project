/**
 * description:        대시보드 한 페이지 — 네 영역을 스크롤만으로 전부 본다
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:            08-dashboard 8.4
 *                     페이지 이동도 탭 전환도 두지 않는다. 시스템은 이 화면의 존재를 모르고,
 *                     둘은 DB로만 만난다 — 여기서 나가는 쓰기 요청은 로그인·로그아웃뿐이다.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  getAccount,
  getAlerts,
  getEquityCurve,
  getTrades,
  getWatchlistBenchmark,
  logout,
  type Axis,
} from './api'
import { fmtAgo, kstDaysAgo } from './format'
import { REFRESH_MS, usePolling } from './usePolling'
import { AccountPanel } from './components/AccountPanel'
import { AlertPanel } from './components/AlertPanel'
import { EquityChart, type Period } from './components/EquityChart'
import { Login, Mark } from './components/Login'
import { TradeReport, type SideFilter } from './components/TradeReport'
import { Badge } from './components/ui'

type Session = 'checking' | 'in' | 'out'

const HEADER_BTN =
  'rounded-md border border-ink-800 px-2 py-1 text-[11px] whitespace-nowrap text-ink-400 ' +
  'transition-colors hover:bg-ink-850 hover:text-ink-50'

/** 기간 토글 → 조회 시작일. 전체는 시작을 비워 서버가 있는 것을 다 준다. */
function periodStart(p: Period): string | undefined {
  if (p === 'all') return undefined
  return kstDaysAgo(p === 'day' ? 1 : p === 'month' ? 31 : 365)
}

export default function App() {
  const [session, setSession] = useState<Session>('checking')

  // 출입증은 화면이 읽을 수 없는 쿠키라(8.6) 로그인 여부를 코드로 확인할 방법이 없다.
  // 그래서 한 번 조회해 보고 401이 오면 로그인 화면을 띄운다.
  useEffect(() => {
    if (session !== 'checking') return
    getAccount()
      .then(() => setSession('in'))
      .catch((e) => setSession(e instanceof ApiError && e.status === 401 ? 'out' : 'in'))
  }, [session])

  if (session === 'checking') {
    return (
      <div className="flex min-h-full items-center justify-center text-xs text-ink-400">
        불러오는 중…
      </div>
    )
  }
  if (session === 'out') return <Login onSuccess={() => setSession('checking')} />
  return <Dashboard onSignedOut={() => setSession('out')} />
}

function Dashboard({ onSignedOut }: { onSignedOut: () => void }) {
  const [axis, setAxis] = useState<Axis>('realized')
  const [period, setPeriod] = useState<Period>('all')
  const [side, setSide] = useState<SideFilter>('all')
  const [range, setRange] = useState({ start: '', end: '' })
  // "N초 전"이 멈춰 있으면 화면이 살아 있는지 알 수 없다 — 10초마다 그 글자만 다시 그린다
  const [, setNow] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setNow((n) => n + 1), 10_000)
    return () => clearInterval(id)
  }, [])

  const unauthorized = useCallback(() => onSignedOut(), [onSignedOut])
  const opts = useMemo(() => ({ onUnauthorized: unauthorized }), [unauthorized])

  const account = usePolling(getAccount, opts)
  const alerts = usePolling(getAlerts, opts)

  const start = periodStart(period)
  const curveFetch = useCallback(() => getEquityCurve({ axis, start }), [axis, start])
  const curve = usePolling(curveFetch, opts)

  const watchFetch = useCallback(() => getWatchlistBenchmark({ start }), [start])
  const watchlist = usePolling(watchFetch, opts)

  const tradesFetch = useCallback(
    () =>
      getTrades({
        side: side === 'all' ? undefined : side,
        start: range.start || undefined,
        end: range.end || undefined,
        limit: 300,
      }),
    [side, range.start, range.end],
  )
  const trades = usePolling(tradesFetch, opts)

  // 그래프의 체결 마커는 종목코드만 들고 온다 — 이미 받아 둔 응답에서 이름을 모아 붙인다
  const names = useMemo(() => {
    const m: Record<string, string> = {}
    for (const h of account.data?.holdings ?? []) if (h.Name) m[h.SymbolId] = h.Name
    for (const o of trades.data?.orders ?? []) if (o.Name) m[o.SymbolId] = o.Name
    for (const p of curve.data?.points ?? []) if (p.Name && p.SymbolId) m[p.SymbolId] = p.Name
    return m
  }, [account.data, trades.data, curve.data])

  const updatedAt = Math.max(
    account.updatedAt ?? 0,
    curve.updatedAt ?? 0,
    trades.updatedAt ?? 0,
    alerts.updatedAt ?? 0,
  )

  const refreshAll = () => {
    account.refresh()
    alerts.refresh()
    curve.refresh()
    watchlist.refresh()
    trades.refresh()
  }

  return (
    <div className="mx-auto min-h-full w-full max-w-[1240px] px-4 pb-20 sm:px-6">
      <Header
        updatedAt={updatedAt || null}
        onRefresh={refreshAll}
        onSignedOut={onSignedOut}
        activeStop={alerts.data?.active_stop ?? false}
      />

      {alerts.data?.active_stop && <StopBanner />}

      <div className="flex flex-col gap-4">
        <AccountPanel data={account.data} error={account.error} loading={account.loading} />
        <EquityChart
          axis={axis}
          onAxisChange={setAxis}
          period={period}
          onPeriodChange={setPeriod}
          data={curve.data}
          watchlist={watchlist.data}
          names={names}
          error={curve.error}
          loading={curve.loading}
        />
        <TradeReport
          data={trades.data}
          error={trades.error}
          loading={trades.loading}
          side={side}
          onSideChange={setSide}
          start={range.start}
          end={range.end}
          onRangeChange={setRange}
        />
        <AlertPanel data={alerts.data} error={alerts.error} loading={alerts.loading} />
      </div>

      <footer className="mt-6 text-center text-[11px] text-ink-700">
        읽기 전용 · {REFRESH_MS / 1000}초마다 갱신
      </footer>
    </div>
  )
}

function Header({
  updatedAt,
  onRefresh,
  onSignedOut,
  activeStop,
}: {
  updatedAt: number | null
  onRefresh: () => void
  onSignedOut: () => void
  activeStop: boolean
}) {
  return (
    <header className="sticky top-0 z-20 -mx-4 mb-4 flex items-center gap-3 border-b border-ink-800 bg-ink-950/85 px-4 py-3 backdrop-blur-md sm:-mx-6 sm:px-6">
      <Mark />
      <span className="text-sm font-semibold tracking-tight">AlphaLoop</span>
      {activeStop && <Badge tone="up">정지 중</Badge>}

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        <span className="font-mono text-[11px] whitespace-nowrap text-ink-400">
          {updatedAt ? fmtAgo(updatedAt) : '대기'}
          <span className="hidden sm:inline"> 갱신</span>
        </span>
        <button type="button" onClick={onRefresh} title="지금 다시 조회" className={HEADER_BTN}>
          새로고침
        </button>
        <button
          type="button"
          onClick={() => void logout().finally(onSignedOut)}
          className={HEADER_BTN}
        >
          나가기
        </button>
      </div>
    </header>
  )
}

/** 지금 정지 중이면 화면 맨 위에서 먼저 말한다 — 아래로 스크롤하기 전에 알아야 한다 */
const StopBanner = () => (
  <div className="mb-4 rounded-xl border border-up/40 bg-up/8 px-4 py-3 text-xs text-up">
    <b className="font-semibold">매매가 정지되어 있습니다.</b> 신규 주문이 막혀 있고 보유 청산만
    계속 돕니다. 맨 아래 오류·정지에서 원인과 해제 방법을 확인하세요.
  </div>
)
