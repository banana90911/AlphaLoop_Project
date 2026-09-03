/**
 * description:        ② 수익 그래프 — 시간순 누적 + 벤치마크
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:            08-dashboard 8.4 ②
 *                     차트는 echarts를 npm 의존성으로 번들에 넣는다. 실행 중 외부 요청 0(8.3).
 */

import * as echarts from 'echarts/core'
import { CustomChart, LineChart, ScatterChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Axis, EquityCurveResponse, FillMarker, WatchlistBenchmark } from '../api'
import { fmtPercent, fmtWon, fmtWonSigned, toMs } from '../format'
import { Chip, Empty, ErrorLine, Panel, Skeleton, Toggle } from './ui'

echarts.use([
  LineChart,
  ScatterChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  SVGRenderer,
])

// x축을 KST 벽시계로 고정한다. 시각을 9시간 밀고 useUTC를 켜면 브라우저 시간대와 무관하게
// 눈금·툴팁이 항상 한국 시간으로 나온다 — 해외에서 열어도 장 마감일이 하루 밀리지 않는다.
const KST_OFFSET = 9 * 3600_000
const kx = (v: string) => toMs(v) + KST_OFFSET

const C = {
  strategy: '#eef1f6',
  kospi: '#8b7cf0',
  kosdaq: '#38b2ac',
  watchlist: '#f0a92c',
  buy: '#22c07d',
  sell: '#f0525f',
  flow: '#8a94a6',
  grid: '#1c212c',
  text: '#7d879a',
}

export const AXIS_OPTIONS: { value: Axis; label: string; short: string; title: string }[] = [
  {
    value: 'realized',
    label: '실현손익 누적',
    short: '실현손익',
    title: '청산한 거래의 손익만 누적. 이체와 무관하므로 입출금 마커를 찍지 않는다.',
  },
  {
    value: 'totalAsset',
    label: '총자산',
    short: '총자산',
    title: '계좌 총자산 시계열. 입금하면 그냥 뛰므로 여기에만 입출금 마커를 찍는다.',
  },
  {
    value: 'twr',
    label: 'TWR 지수',
    short: 'TWR',
    title: '이체 효과를 제거한 시간가중수익률. 비율로 성과를 볼 때 보는 축.',
  },
]

export type Period = 'day' | 'month' | 'year' | 'all'

export const PERIOD_OPTIONS: { value: Period; label: string }[] = [
  { value: 'day', label: '일' },
  { value: 'month', label: '월' },
  { value: 'year', label: '년' },
  { value: 'all', label: '전체' },
]

type Bench = 'kospi' | 'kosdaq' | 'watchlist'

const fmtAxisDate = (ms: number) => {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}.${p(d.getUTCMonth() + 1)}.${p(d.getUTCDate())}`
}

const fmtAxisTime = (ms: number) => {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`
}

export function EquityChart({
  axis,
  onAxisChange,
  period,
  onPeriodChange,
  data,
  watchlist,
  names,
  error,
  loading,
}: {
  axis: Axis
  onAxisChange: (a: Axis) => void
  period: Period
  onPeriodChange: (p: Period) => void
  data: EquityCurveResponse | null
  watchlist: WatchlistBenchmark | null
  /** 종목코드 → 이름. 마커 툴팁이 코드만 보여주지 않게 화면 쪽에서 붙여 준다. */
  names: Record<string, string>
  error: string | null
  loading: boolean
}) {
  const [benches, setBenches] = useState<Record<Bench, boolean>>({
    kospi: true,
    kosdaq: false,
    watchlist: true,
  })
  const toggleBench = (b: Bench) => setBenches((s) => ({ ...s, [b]: !s[b] }))

  const option = useEquityOption(axis, data, watchlist, benches, names)
  const empty = !loading && (data?.points.length ?? 0) === 0

  return (
    <Panel
      title="수익 그래프"
      right={
        <>
          <Toggle options={PERIOD_OPTIONS} value={period} onChange={onPeriodChange} size="sm" />
          <Toggle options={AXIS_OPTIONS} value={axis} onChange={onAxisChange} size="sm" />
        </>
      }
    >
      {error && <ErrorLine message={error} />}

      <div className="flex flex-wrap items-center gap-2 px-5 pt-4">
        <span className="text-[11px] text-ink-400">벤치마크</span>
        <Chip active={benches.kospi} color={C.kospi} onClick={() => toggleBench('kospi')}>
          코스피
        </Chip>
        <Chip active={benches.kosdaq} color={C.kosdaq} onClick={() => toggleBench('kosdaq')}>
          코스닥
        </Chip>
        <Chip
          active={benches.watchlist}
          color={C.watchlist}
          onClick={() => toggleBench('watchlist')}
        >
          균등가중 워치리스트{watchlist ? ` 상위 ${watchlist.top_n}` : ''}
        </Chip>
        <span className="ml-auto flex flex-wrap items-center gap-3 text-[11px] text-ink-400">
          <Legend color={C.buy} shape="circle">
            매수
          </Legend>
          <Legend color={C.sell} shape="circle">
            매도
          </Legend>
          {axis === 'totalAsset' && (
            <Legend color={C.flow} shape="triangle">
              입금 ▲ / 출금 ▼
            </Legend>
          )}
          {axis === 'twr' && (
            <Legend color={C.flow} shape="dash">
              입출금 시점
            </Legend>
          )}
        </span>
      </div>

      {loading && !data ? (
        <Skeleton className="m-5 h-[380px]" />
      ) : empty ? (
        <Empty>이 기간에 그릴 데이터가 없습니다.</Empty>
      ) : (
        <Chart option={option} className="h-[420px] w-full px-2 pb-2" />
      )}
    </Panel>
  )
}

const LEGEND_MARK = {
  circle: (c: string) => ({ width: 6, height: 6, borderRadius: 999, background: c }),
  // 삼각형은 테두리로 그린다 — 아이콘 파일 없이 CSS만으로 끝난다
  triangle: (c: string) => ({
    width: 0,
    height: 0,
    borderLeft: '4px solid transparent',
    borderRight: '4px solid transparent',
    borderBottom: `7px solid ${c}`,
  }),
  dash: (c: string) => ({ width: 12, height: 0, borderTop: `1px dashed ${c}` }),
} as const

const Legend = ({
  color,
  shape,
  children,
}: {
  color: string
  shape: keyof typeof LEGEND_MARK
  children: ReactNode
}) => (
  <span className="inline-flex items-center gap-1.5">
    <span style={LEGEND_MARK[shape](color)} />
    {children}
  </span>
)

/**
 * echarts 인스턴스 하나를 붙잡고 option만 갈아끼운다 — 갱신 때 차트를 새로 만들지 않는다.
 *
 * 렌더러는 SVG다. 캔버스는 기기 화소 배율에 맞춰 미리 크게 그려 두고 줄이는 방식이라,
 * 배율이 바뀌거나(모니터 이동·브라우저 확대) 부모가 합성 레이어로 밀려나면 그 배율이
 * 어긋나 뭉개진다. SVG는 래스터화 단계가 아예 없어 브라우저가 매번 화면 해상도로 직접
 * 그린다 — 배율을 맞출 필요 자체가 없어진다.
 */
function Chart({ option, className }: { option: echarts.EChartsCoreOption; className?: string }) {
  const host = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!host.current) return
    chart.current = echarts.init(host.current, null, { renderer: 'svg' })
    const ro = new ResizeObserver(() => chart.current?.resize())
    ro.observe(host.current)
    return () => {
      ro.disconnect()
      chart.current?.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    // notMerge=false로 확대해 둔 위치(dataZoom)를 60초 갱신이 되돌리지 않게 하되,
    // series만은 통째로 갈아끼운다 — 그냥 병합하면 벤치마크를 끄고 series 배열에서
    // 빼도 옛 계열이 차트에 그대로 남아 토글이 안 먹는 것처럼 보인다.
    chart.current?.setOption(option, { notMerge: false, replaceMerge: ['series'], lazyUpdate: true })
  }, [option])

  return <div ref={host} className={className} />
}

function useEquityOption(
  axis: Axis,
  data: EquityCurveResponse | null,
  watchlist: WatchlistBenchmark | null,
  benches: Record<Bench, boolean>,
  names: Record<string, string>,
): echarts.EChartsCoreOption {
  // 곡선 위의 값을 시각으로 찾아본다 — 체결·이체 마커를 곡선에 얹기 위한 조회표
  const points = data?.points ?? []

  const curve = useMemo(() => {
    return points
      .map((p) => {
        const at = p.RecordedDateTime ?? p.ExitDate ?? p.TradeDate
        if (!at || p.Cumulative === null || p.Cumulative === undefined) return null
        const y = axis === 'twr' ? (p.Cumulative - 1) * 100 : p.Cumulative
        return { value: [kx(at), y] as [number, number], point: p }
      })
      .filter((v): v is { value: [number, number]; point: (typeof points)[number] } => v !== null)
  }, [points, axis])

  const valueAt = useCallback(
    (ms: number): number | null => {
      if (curve.length === 0) return null
      let lo = 0
      let hi = curve.length - 1
      if (ms <= curve[0].value[0]) return curve[0].value[1]
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1
        if (curve[mid].value[0] <= ms) lo = mid
        else hi = mid - 1
      }
      return curve[lo].value[1]
    },
    [curve],
  )

  return useMemo(() => {
    const isPercentLeft = axis === 'twr'
    const benchAxis = isPercentLeft ? 0 : 1
    const unit = isPercentLeft ? '%' : '원'

    // 지수는 절대값이 아니라 기간 시작 대비 변화율로 겹쳐야 우리 곡선과 비교가 된다
    const normalize = (rows: { x: number; close: number }[]) => {
      if (rows.length === 0) return []
      const base = rows[0].close
      return rows.map((r) => [r.x, (r.close / base - 1) * 100] as [number, number])
    }

    const indexRows = (code: 'KOSPI' | 'KOSDAQ') =>
      normalize(
        (data?.benchmarks ?? [])
          .filter((b) => b.IndexCode === code)
          .map((b) => ({ x: kx(b.TradeDate), close: Number(b.Close) })),
      )

    // 툴팁이 params에만 기대면 그 x에 점이 없는 계열은 통째로 빠진다(실현손익은 청산일에만
    // 점이 있다). 그래서 계열마다 조회표를 따로 들고, 툴팁은 여기서 값을 찾아 채운다.
    const lookups: { name: string; color: string; rows: [number, number][] }[] = []

    const line = (name: string, color: string, values: [number, number][]) => ({
      name,
      type: 'line' as const,
      yAxisIndex: benchAxis,
      data: values,
      showSymbol: false,
      symbol: 'none',
      lineStyle: { width: 1.2, color, type: 'dashed' as const },
      itemStyle: { color },
      z: 2,
    })

    const series: Record<string, unknown>[] = [
      {
        name: AXIS_OPTIONS.find((o) => o.value === axis)!.label,
        type: 'line',
        yAxisIndex: 0,
        data: curve,
        showSymbol: false,
        symbol: 'none',
        // 실현손익은 청산 시점에만 계단식으로 바뀐다 — 사이를 직선으로 이으면
        // 아무 일도 없던 날에 수익이 흐른 것처럼 보인다
        step: axis === 'realized' ? 'end' : false,
        lineStyle: { width: 2, color: C.strategy },
        itemStyle: { color: C.strategy },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(238,241,246,0.14)' },
            { offset: 1, color: 'rgba(238,241,246,0)' },
          ]),
        },
        z: 3,
      },
    ]

    if (benches.kospi) {
      const rows = indexRows('KOSPI')
      series.push(line('코스피', C.kospi, rows))
      lookups.push({ name: '코스피', color: C.kospi, rows })
    }
    if (benches.kosdaq) {
      const rows = indexRows('KOSDAQ')
      series.push(line('코스닥', C.kosdaq, rows))
      lookups.push({ name: '코스닥', color: C.kosdaq, rows })
    }
    if (benches.watchlist && watchlist) {
      const rows = watchlist.series.map(
        (s) => [kx(s.TradeDate), s.Cumulative * 100] as [number, number],
      )
      series.push(line('균등가중 워치리스트', C.watchlist, rows))
      lookups.push({ name: '균등가중 워치리스트', color: C.watchlist, rows })
    }

    // 곡선 위에 매수·매도 시점을 점으로 — 어느 구간의 상승이 어느 거래에서 나왔는지 잇는다.
    // 사이클은 하루 한 번이라 같은 날 체결은 한 점에 모인다. 그 점에 매수와 매도가 같이
    // 있으면 동그라미 두 개를 겹치지 않고 하나를 세로로 갈라 왼쪽 초록·오른쪽 빨강으로 칠한다.
    const marks = fillMarks(data?.markers ?? [], valueAt)
    if (marks.length > 0) {
      series.push({
        name: '체결',
        type: 'custom',
        coordinateSystem: 'cartesian2d',
        data: marks,
        renderItem: (
          params: { dataIndex: number },
          api: { coord: (v: number[]) => number[] },
        ) => {
          const m = marks[params.dataIndex]
          if (!m) return null
          const [x, y] = api.coord(m.value)
          // 곡선을 덮지 않을 만큼만. 반씩 나뉜 점은 조금 커야 두 색이 구분된다
          const r = m.kind === 'both' ? 5 : 4
          const stroke = { stroke: '#0a0c10', lineWidth: 1 }
          if (m.kind !== 'both') {
            return {
              type: 'circle',
              shape: { cx: x, cy: y, r },
              style: { fill: m.kind === 'buy' ? C.buy : C.sell, ...stroke },
            }
          }
          // 반원 두 쪽. 화면 좌표는 y가 아래로 커지므로 각도 0이 3시 방향이고,
          // -90°→90°를 시계방향으로 쓸면 오른쪽 반이 된다.
          const half = (from: number, to: number, fill: string) => ({
            type: 'sector',
            shape: { cx: x, cy: y, r, r0: 0, startAngle: from, endAngle: to, clockwise: true },
            style: { fill, ...stroke },
          })
          return {
            type: 'group',
            children: [
              half(Math.PI / 2, Math.PI * 1.5, C.buy),    // 왼쪽 — 매수
              half(-Math.PI / 2, Math.PI / 2, C.sell),    // 오른쪽 — 매도
            ],
          }
        },
        z: 5,
      })
    }

    // 입출금 마커는 실현손익 축에 찍지 않는다 — 그 축은 이체와 무관해서,
    // 찍으면 없는 인과를 암시한다(8.4 ②)
    const flows = data?.flow_markers ?? []
    if (axis === 'totalAsset' && flows.length > 0) {
      series.push({
        name: '입출금',
        type: 'scatter',
        yAxisIndex: 0,
        data: flows
          .map((f) => {
            const x = kx(f.DetectedDateTime)
            const y = valueAt(x)
            return y === null ? null : { value: [x, y], flow: f }
          })
          .filter(Boolean),
        symbol: (_: unknown, p: { data?: { flow?: { Direction: string } } }) =>
          p.data?.flow?.Direction === 'withdrawal' ? 'triangle' : 'triangle',
        symbolRotate: (_: unknown, p: { data?: { flow?: { Direction: string } } }) =>
          p.data?.flow?.Direction === 'withdrawal' ? 180 : 0,
        symbolSize: 11,
        itemStyle: { color: C.flow, borderColor: '#0a0c10', borderWidth: 1 },
        z: 6,
      })
    }
    if (axis === 'twr' && flows.length > 0) {
      // TWR 축에서는 이체가 곡선을 움직이지 않는다 — 언제 있었는지만 회색 점선으로 표시
      series.push({
        name: '입출금 시점',
        type: 'line',
        yAxisIndex: 0,
        data: [],
        markLine: {
          silent: false,
          symbol: 'none',
          lineStyle: { color: C.flow, type: 'dashed', width: 1 },
          label: { show: false },
          data: flows.map((f) => ({
            xAxis: kx(f.DetectedDateTime),
            flow: f,
          })),
        },
        z: 1,
      })
    }

    return {
      useUTC: true,
      animation: false,
      backgroundColor: 'transparent',
      grid: {
        left: 16,
        right: 16,
        top: 24,
        bottom: 64,
        containLabel: true,
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'line', lineStyle: { color: '#2a313f' } },
        backgroundColor: 'rgba(16,19,26,0.96)',
        borderColor: '#2a313f',
        borderWidth: 1,
        padding: [8, 10],
        textStyle: { color: '#eef1f6', fontSize: 12 },
        extraCssText: 'backdrop-filter: blur(6px); border-radius: 10px;',
        formatter: (params: TooltipParam[]) =>
          tooltip(params, {
            axis,
            unit,
            strategyName: AXIS_OPTIONS.find((o) => o.value === axis)!.label,
            valueAt,
            lookups,
            names,
          }),
      },
      xAxis: {
        type: 'time',
        axisLine: { lineStyle: { color: C.grid } },
        axisLabel: {
          color: C.text,
          fontSize: 11,
          hideOverlap: true,
          // 기본 포맷터는 영어 월 약어(Nov·Mar)를 쓴다 — 눈금 단위별로 한국어를 지정한다
          formatter: {
            year: '{yyyy}',
            month: '{yyyy}.{MM}',
            day: '{MM}.{dd}',
            hour: '{HH}:{mm}',
            minute: '{HH}:{mm}',
          },
        },
        splitLine: { show: false },
        axisPointer: { label: { show: false } },
      },
      yAxis: [
        {
          type: 'value',
          // 확대하면 보이는 구간에 상단·하단을 다시 맞춘다(8.4 ②)
          scale: true,
          position: 'left',
          axisLabel: {
            color: C.text,
            fontSize: 11,
            formatter: (v: number) =>
              isPercentLeft ? `${v.toFixed(0)}%` : shortWon(v),
          },
          splitLine: { lineStyle: { color: C.grid } },
        },
        {
          type: 'value',
          scale: true,
          position: 'right',
          show: !isPercentLeft,
          axisLabel: { color: C.text, fontSize: 11, formatter: (v: number) => `${v.toFixed(0)}%` },
          splitLine: { show: false },
        },
      ],
      // 확대·좌우 스크롤. 휠은 확대, 드래그는 이동
      dataZoom: [
        { type: 'inside', filterMode: 'filter', zoomOnMouseWheel: true, moveOnMouseMove: true },
        {
          type: 'slider',
          filterMode: 'filter',
          height: 26,
          bottom: 12,
          borderColor: C.grid,
          backgroundColor: 'rgba(28,33,44,0.4)',
          fillerColor: 'rgba(42,49,63,0.6)',
          handleStyle: { color: '#3a4354', borderColor: '#556' },
          moveHandleStyle: { color: '#3a4354' },
          dataBackground: {
            lineStyle: { color: C.text, opacity: 0.5 },
            areaStyle: { color: C.text, opacity: 0.15 },
          },
          selectedDataBackground: {
            lineStyle: { color: C.strategy, opacity: 0.8 },
            areaStyle: { color: C.strategy, opacity: 0.2 },
          },
          textStyle: { color: C.text, fontSize: 10 },
          labelFormatter: (v: number) => fmtAxisDate(v),
        },
      ],
      series,
    }
  }, [axis, curve, data, watchlist, benches, valueAt])
}

type TooltipParam = {
  seriesName: string
  seriesType: string
  color: string
  value: [number, number]
  data?: {
    point?: {
      Name?: string | null
      SymbolId?: string
      NetProfitLoss?: number
      RMultiple?: number | null
      ExitReason?: string | null
      ReturnPercent?: number | null
    }
    fills?: FillMarker[]
    flow?: { Kind: string; Amount: number; Direction: string }
  }
}

const EXIT_REASON: Record<string, string> = {
  breakeven: '본전',
  stopHit: '손절',
  timeExit: '시간 청산',
  thesisInvalid: '근거 소멸',
  trail: '트레일링',
}

const FLOW_KIND: Record<string, string> = {
  deposit: '입금',
  withdrawal: '출금',
  dividend: '배당',
  taxRefund: '세금 환급',
  interest: '이자',
  fee: '수수료',
  unknown: '미분류',
}

type FillMark = {
  value: [number, number]
  kind: 'buy' | 'sell' | 'both'
  fills: FillMarker[]
}

/**
 * 체결을 거래일 단위로 묶어 곡선 위에 얹을 점으로 만든다.
 *
 * 한 사이클에서 나간 주문들은 몇 초 차이로 체결되므로 따로 찍으면 어차피 한 픽셀에
 * 겹쳐 보인다. 그럴 바에는 하나로 묶고, 그 안에 매수와 매도가 같이 있으면 반씩 칠해
 * "이 날 사고 팔았다"를 한 점으로 읽히게 한다.
 */
function fillMarks(markers: FillMarker[], valueAt: (ms: number) => number | null): FillMark[] {
  const byDay = new Map<number, FillMarker[]>()
  for (const m of markers) {
    const x = kx(m.FilledDateTime)
    const day = Math.floor(x / 86400_000)      // kx가 KST를 UTC 자리로 옮겨 놨다
    const bucket = byDay.get(day)
    if (bucket) bucket.push(m)
    else byDay.set(day, [m])
  }

  const out: FillMark[] = []
  for (const fills of byDay.values()) {
    // 그날 첫 체결 시각에 찍는다 — 자정으로 몰면 점이 하루만큼 왼쪽으로 밀린다
    const x = Math.min(...fills.map((f) => kx(f.FilledDateTime)))
    const y = valueAt(x)
    if (y === null) continue
    const hasBuy = fills.some((f) => f.Side === 'buy')
    const hasSell = fills.some((f) => f.Side === 'sell')
    out.push({
      value: [x, y],
      kind: hasBuy && hasSell ? 'both' : hasBuy ? 'buy' : 'sell',
      fills,
    })
  }
  return out.sort((a, b) => a.value[0] - b.value[0])
}

/** 시각 하나에서 그 계열의 값을 찾는다(그 시점 이전의 마지막 값). */
function seek(rows: [number, number][], ms: number): number | null {
  if (rows.length === 0 || ms < rows[0][0]) return null
  let lo = 0
  let hi = rows.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (rows[mid][0] <= ms) lo = mid
    else hi = mid - 1
  }
  return rows[lo][1]
}

type TooltipCtx = {
  axis: Axis
  unit: string
  strategyName: string
  valueAt: (ms: number) => number | null
  lookups: { name: string; color: string; rows: [number, number][] }[]
  names: Record<string, string>
}

function tooltip(params: TooltipParam[], ctx: TooltipCtx): string {
  const anchor = params.find((p) => Array.isArray(p.value))
  if (!anchor) return ''
  const ms = anchor.value[0]

  const dot = (c: string) =>
    `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${c};margin-right:6px"></span>`
  const row = (color: string, label: string, value: string) =>
    `<div style="display:flex;justify-content:space-between;gap:20px;line-height:1.7">` +
    `<span>${dot(color)}${label}</span><b>${value}</b></div>`
  const pct = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)}%`

  const out: string[] = [
    `<div style="color:#7d879a;font-size:11px;margin-bottom:6px">${fmtAxisDate(ms)}${
      ctx.axis === 'realized' ? '' : ` ${fmtAxisTime(ms)}`
    }</div>`,
  ]

  // 곡선과 벤치마크는 params에 없더라도 조회표에서 찾아 항상 같이 보여준다 —
  // 비교하려고 겹쳐 그린 것이라 하나만 뜨면 볼 이유가 없다
  const mine = ctx.valueAt(ms)
  if (mine !== null) {
    out.push(
      row(C.strategy, ctx.strategyName, ctx.unit === '%' ? pct(mine) : `${fmtWon(mine)}원`),
    )
  }
  for (const l of ctx.lookups) {
    const v = seek(l.rows, ms)
    if (v !== null) out.push(row(l.color, l.name, pct(v)))
  }

  // 그 시각의 체결·이체 — 어느 거래가 이 구간을 만들었는지 잇는 부분
  const events: string[] = []
  for (const p of params) {
    for (const f of p.data?.fills ?? []) {
      events.push(
        `<div style="line-height:1.7">${dot(f.Side === 'buy' ? C.buy : C.sell)}` +
          `${f.Side === 'buy' ? '매수' : '매도'} ${ctx.names[f.SymbolId] ?? f.SymbolId} ` +
          `<span style="color:#7d879a">${f.FilledQuantity}주 @ ${fmtWon(f.AverageFillPrice)}</span></div>`,
      )
    }
    if (p.data?.flow) {
      const f = p.data.flow
      events.push(
        `<div style="line-height:1.7">${dot(C.flow)}${FLOW_KIND[f.Kind] ?? f.Kind} ` +
          `<span style="color:#7d879a">금액: ${fmtWonSigned(f.Amount)}원</span></div>`,
      )
    }
    // 실현손익 축의 점은 그 자체가 한 건의 청산이다
    const pt = p.data?.point
    if (pt && pt.NetProfitLoss !== undefined) {
      events.push(
        `<div style="line-height:1.7;color:#c3cad6">${ctx.names[pt.SymbolId ?? ''] ?? pt.Name ?? pt.SymbolId}` +
          ` <span style="color:#7d879a">${pt.SymbolId}</span></div>` +
          `<div style="color:#7d879a;line-height:1.7">청산 <b style="color:${
            (pt.NetProfitLoss ?? 0) >= 0 ? C.sell : '#4e8ef7'
          }">${fmtWonSigned(pt.NetProfitLoss)}원</b>` +
          ` · ${fmtPercent(pt.ReturnPercent)}` +
          ` · R ${pt.RMultiple === null || pt.RMultiple === undefined ? '—' : pt.RMultiple.toFixed(2)}` +
          `${pt.ExitReason ? ` · ${EXIT_REASON[pt.ExitReason] ?? pt.ExitReason}` : ''}</div>`,
      )
    }
  }
  if (events.length > 0) {
    out.push(
      `<div style="margin-top:5px;padding-top:5px;border-top:1px solid #2a313f">${events.join('')}</div>`,
    )
  }
  return out.join('')
}

/** 축 눈금용 짧은 금액 — 1.2억 / 340만 */
function shortWon(v: number): string {
  const abs = Math.abs(v)
  const sign = v < 0 ? '−' : ''
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(1)}억`
  if (abs >= 1e4) return `${sign}${Math.round(abs / 1e4).toLocaleString('ko-KR')}만`
  return `${sign}${Math.round(abs).toLocaleString('ko-KR')}`
}
