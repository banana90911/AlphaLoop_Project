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
  SnapshotId: string
  CycleId: string
  TradeDate: string
  Amount: number            // 예수금
  PositionValue: number
  TotalAsset: number
  BaseAsset: number | null
  CumulativeNetFlow: number
  TwrIndex: number | null
  DayReturnPercent: number | null
  RecordedDateTime: string
}

export type Holding = {
  PositionId: string
  SymbolId: string
  Name: string | null
  Quantity: number
  AveragePrice: number
  CurrentStopPrice: number | null
  InitialStopPrice: number | null
  EntryDate: string | null
  LastPrice: number | null
  PricedAt: string | null
  ProfitLoss: number | null
  ReturnPercent: number | null
  HoldingDays: number | null
}

export type AccountResponse = {
  snapshot: Snapshot | null
  holdings: Holding[]
  cumulativeNetFlow: number
  twrReturn: number | null
  safeWithdrawable: number | null
}

export const getAccount = () => request<AccountResponse>('/api/account')

// ── ② 수익 그래프 ──────────────────────────────────────────────
export type Axis = 'realized' | 'totalAsset' | 'twr'

export type CurvePoint = {
  Cumulative: number | null
  // realized 축
  OutcomeId?: string
  SymbolId?: string
  Name?: string | null
  ExitDate?: string
  NetProfitLoss?: number
  RMultiple?: number | null
  ExitReason?: string | null
  ReturnPercent?: number | null
  // totalAsset·twr 축
  TradeDate?: string
  TotalAsset?: number
  TwrIndex?: number | null
  DayReturnPercent?: number | null
  RecordedDateTime?: string
}

export type FillMarker = {
  ClientOrderId: string
  SymbolId: string
  Side: 'buy' | 'sell'
  Purpose: string
  FilledQuantity: number
  AverageFillPrice: number | null
  FilledDateTime: string
}

export type FlowMarker = {
  FlowId: string
  TradeDate: string
  Kind: string
  Amount: number
  Status: string
  Source: string
  ExpectedCash: number
  ActualCash: number
  DetectedDateTime: string
  Direction: 'deposit' | 'withdrawal'
}

export type Benchmark = { IndexCode: 'KOSPI' | 'KOSDAQ'; TradeDate: string; Close: number }

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
  series: { TradeDate: string; Names: number; Cumulative: number }[]
}

export const getWatchlistBenchmark = (p: { start?: string; end?: string }) =>
  request<WatchlistBenchmark>('/api/benchmark/watchlist' + qs(p))

// ── ③ 거래 리포트 ──────────────────────────────────────────────
export type Order = {
  ClientOrderId: string
  CycleId: string | null
  DecisionId: string | null
  KisOrderNo: string | null
  SymbolId: string
  Name: string | null
  Side: 'buy' | 'sell'
  Purpose: 'entry' | 'stop' | 'stopAmend' | 'exit'
  OrderType: string
  OrderQuantity: number
  OrderPrice: number | null
  TriggerPrice: number | null
  FilledQuantity: number
  AverageFillPrice: number | null
  Fee: number | null
  Tax: number | null
  Status: 'submitted' | 'partial' | 'filled' | 'cancelled' | 'rejected'
  OrderedDateTime: string
  FilledDateTime: string | null
  Mode: string
  // 아래 둘은 "Orders"에 없고 조회가 조인해 붙여 준다(손절가·보유 여부)
  StopPrice: number | null
  PositionStatus: 'open' | 'closed' | 'frozen' | null
}

export type CashFlow = {
  FlowId: string
  DetectedCycleId: string | null
  TradeDate: string
  Kind: string
  Amount: number
  Status: 'unconfirmed' | 'confirmed' | 'reclassified'
  Source: string
  ExpectedCash: number
  ActualCash: number
  Note: string | null
  DetectedDateTime: string
  ConfirmedDateTime: string | null
  ConfirmedBy: string | null
  Mode: string
}

export type TradesResponse = { orders: Order[]; flows: CashFlow[] }

export const getTrades = (p: {
  start?: string
  end?: string
  side?: 'buy' | 'sell' | 'flow'
  limit?: number
}) => request<TradesResponse>('/api/trades' + qs(p))

export type Decision = {
  DecisionId: string
  Action: string
  Reason: string
  Score: number | null
  Threshold: number | null
  EntryPrice: number | null
  StopPrice: number | null
  RiskPerShare: number | null
  TargetPositions: number | null
  Quantity: number | null
  RewardRiskRatio: number | null
  EstimatedCost: number | null
  NetEdge: number | null
  Regime: string | null
  DecidedDateTime: string
}

export type CycleScore = {
  Inclusion: string
  BaseScore: number | null
  FlowPercentileLive: number | null
  TotalScore: number | null
  LastPrice: number | null
  Atr: number | null
  StopWidth: number | null
  IsTradable: boolean | null
  BlockReason: string | null
  ScoredDateTime: string
}

export type RiskCheck = {
  CheckId: string
  CheckOrder: number
  CheckName: string
  Result: 'pass' | 'reject' | 'reduce' | 'skipCycle' | 'safeStop' | 'flowDetected'
  Reason: string | null
  LimitValue: number | null
  ActualValue: number | null
  CheckedDateTime: string
}

export type Outcome = {
  OutcomeId: string
  SymbolId: string
  EntryPrice: number | null
  ExitPrice: number | null
  Quantity: number | null
  EntryDate: string | null
  ExitDate: string | null
  HoldingDays: number | null
  GrossProfitLoss: number | null
  Fee: number | null
  Tax: number | null
  NetProfitLoss: number | null
  ReturnPercent: number | null
  RMultiple: number | null
  ExitReason: string | null
  EntryScore: number | null
  EntryRegime: string | null
  ClosedDateTime: string
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
  EventId: string
  CycleId: string | null
  OccurredDateTime: string
  Cause: string
  Trigger: 'auto' | 'manual'
  ReleasedDateTime: string | null
  ReleasedBy: string | null
  ReleaseReason: string | null
}

export type FailedCycle = {
  CycleId: string
  TradeDate: string
  Status: 'failed' | 'skipped'
  FailedStep: number | null
  SkipReason: string | null
  StartedDateTime: string
}

export type IngestRun = {
  RunId: string
  TargetTable: string
  Source: string
  Status: 'ok' | 'partial' | 'failed'
  TargetCount: number | null
  SuccessCount: number | null
  RowsWritten: number | null
  ErrorMessage: string | null
  StartedDateTime: string
  FinishedDateTime: string | null
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
