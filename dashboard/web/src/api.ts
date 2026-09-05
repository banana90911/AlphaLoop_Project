/**
 * description:        조회 API 클라이언트 — dashboard/api.py 계약의 타입 사본
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:            읽기 전용. 여기서 쓰기 요청을 보내는 함수는 로그인·로그아웃뿐이다.
 */

// 화면(Vercel)과 API(NCP)는 따로 배포된다. 개발 중에는 빈 값이라 Vite 프록시를 탄다.
const BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    // 출입증은 화면 코드가 읽을 수 없는 쿠키에 담긴다(8.6). 그래서 매 요청에 쿠키를 태운다.
    credentials: 'include',
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new ApiError(res.status, detail?.detail ?? `요청이 실패했습니다 (${res.status})`)
  }
  return res.status === 204 ? (undefined as T) : res.json()
}

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') q.set(k, String(v))
  const s = q.toString()
  return s ? `?${s}` : ''
}

// ── ① 나의 정보 ────────────────────────────────────────────────
export type Snapshot = {
  snapshot_id: string
  cycle_id: string
  trade_date: string
  amount: number            // 예수금
  position_value: number
  total_asset: number
  base_asset: number | null
  cumulative_net_flow: number
  twr_index: number | null
  day_return_percent: number | null
  recorded_date_time: string
}

export type Holding = {
  position_id: string
  symbol_id: string
  name: string | null
  quantity: number
  average_price: number
  current_stop_price: number | null
  initial_stop_price: number | null
  entry_date: string | null
  last_price: number | null
  priced_at: string | null
  profit_loss: number | null
  return_percent: number | null
  holding_days: number | null
}

export type AccountResponse = {
  snapshot: Snapshot | null
  holdings: Holding[]
  cumulative_net_flow: number
  twr_return: number | null
  safe_withdrawable: number | null
}

export const getAccount = () => request<AccountResponse>('/api/account')

// ── ② 수익 그래프 ──────────────────────────────────────────────
export type Axis = 'realized' | 'totalAsset' | 'twr'

export type CurvePoint = {
  cumulative: number | null
  // realized 축
  outcome_id?: string
  symbol_id?: string
  name?: string | null
  exit_date?: string
  net_profit_loss?: number
  r_multiple?: number | null
  exit_reason?: string | null
  return_percent?: number | null
  // totalAsset·twr 축
  trade_date?: string
  total_asset?: number
  twr_index?: number | null
  day_return_percent?: number | null
  recorded_date_time?: string
}

export type FillMarker = {
  client_order_id: string
  symbol_id: string
  side: 'buy' | 'sell'
  purpose: string
  filled_quantity: number
  average_fill_price: number | null
  filled_date_time: string
}

export type FlowMarker = {
  flow_id: string
  trade_date: string
  kind: string
  amount: number
  status: string
  source: string
  expected_cash: number
  actual_cash: number
  detected_date_time: string
  direction: 'deposit' | 'withdrawal'
}

export type Benchmark = { index_code: 'KOSPI' | 'KOSDAQ'; trade_date: string; close: number }

export type EquityCurveResponse = {
  axis: Axis
  points: CurvePoint[]
  benchmarks: Benchmark[]
  markers: FillMarker[]
  flow_markers: FlowMarker[]
}

export const getEquityCurve = (p: { axis: Axis; start?: string; end?: string }) =>
  request<EquityCurveResponse>('/api/equity-curve' + qs(p))

export type WatchlistBenchmark = {
  top_n: number
  series: { trade_date: string; names: number; cumulative: number }[]
}

export const getWatchlistBenchmark = (p: { start?: string; end?: string }) =>
  request<WatchlistBenchmark>('/api/benchmark/watchlist' + qs(p))

// ── ③ 거래 리포트 ──────────────────────────────────────────────
export type Order = {
  client_order_id: string
  cycle_id: string | null
  decision_id: string | null
  kis_order_no: string | null
  symbol_id: string
  name: string | null
  side: 'buy' | 'sell'
  purpose: 'entry' | 'stop' | 'stopAmend' | 'exit'
  order_type: string
  order_quantity: number
  order_price: number | null
  trigger_price: number | null
  filled_quantity: number
  average_fill_price: number | null
  fee: number | null
  tax: number | null
  status: 'submitted' | 'partial' | 'filled' | 'cancelled' | 'rejected'
  ordered_date_time: string
  filled_date_time: string | null
  mode: string
  // 아래 둘은 "Orders"에 없고 조회가 조인해 붙여 준다(손절가·보유 여부)
  stop_price: number | null
  position_status: 'open' | 'closed' | 'frozen' | null
}

export type CashFlow = {
  flow_id: string
  detected_cycle_id: string | null
  trade_date: string
  kind: string
  amount: number
  status: 'unconfirmed' | 'confirmed' | 'reclassified'
  source: string
  expected_cash: number
  actual_cash: number
  note: string | null
  detected_date_time: string
  confirmed_date_time: string | null
  confirmed_by: string | null
  mode: string
}

export type TradesResponse = { orders: Order[]; flows: CashFlow[] }

export const getTrades = (p: {
  start?: string
  end?: string
  side?: 'buy' | 'sell' | 'flow'
  limit?: number
}) => request<TradesResponse>('/api/trades' + qs(p))

export type Decision = {
  decision_id: string
  action: string
  reason: string
  score: number | null
  threshold: number | null
  entry_price: number | null
  stop_price: number | null
  risk_per_share: number | null
  target_positions: number | null
  quantity: number | null
  reward_risk_ratio: number | null
  estimated_cost: number | null
  net_edge: number | null
  regime: string | null
  decided_date_time: string
}

export type CycleScore = {
  inclusion: string
  base_score: number | null
  flow_percentile_live: number | null
  total_score: number | null
  last_price: number | null
  atr: number | null
  stop_width: number | null
  is_tradable: boolean | null
  block_reason: string | null
  scored_date_time: string
}

export type RiskCheck = {
  check_id: string
  check_order: number
  check_name: string
  result: 'pass' | 'reject' | 'reduce' | 'skipCycle' | 'safeStop' | 'flowDetected'
  reason: string | null
  limit_value: number | null
  actual_value: number | null
  checked_date_time: string
}

export type Outcome = {
  outcome_id: string
  symbol_id: string
  entry_price: number | null
  exit_price: number | null
  quantity: number | null
  entry_date: string | null
  exit_date: string | null
  holding_days: number | null
  gross_profit_loss: number | null
  fee: number | null
  tax: number | null
  net_profit_loss: number | null
  return_percent: number | null
  r_multiple: number | null
  exit_reason: string | null
  entry_score: number | null
  entry_regime: string | null
  closed_date_time: string
}

export type TradeDetail = {
  order: Order
  decision: Decision | null
  cycle_score: CycleScore | null
  risk_checks: RiskCheck[]
  outcome: Outcome | null
}

export const getTradeDetail = (clientOrderId: string) =>
  request<TradeDetail>(`/api/trades/${encodeURIComponent(clientOrderId)}`)

// ── ④ 오류·정지 ────────────────────────────────────────────────
export type SafeStopEvent = {
  event_id: string
  cycle_id: string | null
  occurred_date_time: string
  cause: string
  trigger: 'auto' | 'manual'
  released_date_time: string | null
  released_by: string | null
  release_reason: string | null
}

export type FailedCycle = {
  cycle_id: string
  trade_date: string
  status: 'failed' | 'skipped'
  failed_step: number | null
  skip_reason: string | null
  started_date_time: string
}

export type IngestRun = {
  run_id: string
  target_table: string
  source: string
  status: 'ok' | 'partial' | 'failed'
  target_count: number | null
  success_count: number | null
  rows_written: number | null
  error_message: string | null
  started_date_time: string
  finished_date_time: string | null
}

export type AlertsResponse = {
  safe_stops: SafeStopEvent[]
  active_stop: boolean
  failed_cycles: FailedCycle[]
  failed_ingests: IngestRun[]
  unlabeled_flows: CashFlow[]
  unlabeled_flow_hint: string
}

export const getAlerts = () => request<AlertsResponse>('/api/alerts')

// ── 로그인 ─────────────────────────────────────────────────────
export const login = (password: string) =>
  request<{ ok: true; expires_hours: number }>('/api/login', {
    method: 'POST',
    body: JSON.stringify({ password }),
  })

export const logout = () => request<{ ok: true }>('/api/logout', { method: 'POST' })
