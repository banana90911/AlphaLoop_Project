/**
 * description:        ① 나의 정보 — 잔고·보유 종목·평가손익
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:            08-dashboard 8.4 ①
 */

import { useState } from 'react'
import type { AccountResponse, Holding } from '../api'
import {
  fmtDate,
  fmtPercent,
  fmtStamp,
  fmtWon,
  fmtWonShort,
  fmtWonSigned,
  signColor,
} from '../format'
import { Badge, Empty, ErrorLine, Panel, Skeleton, Stat } from './ui'

// 보유가 20종목까지 가므로 기본은 상위 5줄만 펼쳐 두고 나머지는 "더보기"로 접는다(8.4 ①)
const COLLAPSED_ROWS = 5

export function AccountPanel({
  data,
  error,
  loading,
}: {
  data: AccountResponse | null
  error: string | null
  loading: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const snap = data?.snapshot ?? null

  // 총자본 − 누적 순입금 = 누적 손익. 두 숫자가 서로를 검산한다(8.4 ①)
  const cumulativeProfit =
    snap && data ? Number(snap.TotalAsset) - Number(data.cumulativeNetFlow) : null
  const evaluated = data?.holdings.reduce((s, h) => s + (h.ProfitLoss ?? 0), 0) ?? null
  // 평가 기준 시각 = 마지막 사이클이 가격을 본 시점. 가장 낡은 것을 대표로 쓴다
  const pricedAt = data?.holdings
    .map((h) => h.PricedAt)
    .filter((v): v is string => !!v)
    .sort()[0]

  const holdings = data?.holdings ?? []
  const shown = expanded ? holdings : holdings.slice(0, COLLAPSED_ROWS)

  return (
    <Panel
      title="나의 정보"
      subtitle="잔고 · 보유 종목 · 평가손익"
      right={
        snap && (
          <span className="font-mono text-[11px] text-ink-400">
            {fmtDate(snap.TradeDate)} 기준
          </span>
        )
      }
    >
      {error && <ErrorLine message={error} />}

      {loading && !data ? (
        <div className="grid grid-cols-2 gap-px bg-ink-800 lg:grid-cols-4">
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="bg-ink-900 px-5 py-4">
              <Skeleton className="mb-2 h-3 w-16" />
              <Skeleton className="h-6 w-24" />
            </div>
          ))}
        </div>
      ) : !snap ? (
        <Empty>아직 계좌 스냅샷이 없습니다. 첫 사이클이 돌면 채워집니다.</Empty>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-px bg-ink-800 lg:grid-cols-4">
            <Stat
              label="총자본"
              size="lg"
              value={`${fmtWon(snap.TotalAsset)}원`}
              hint={`예수금 ${fmtWonShort(snap.Amount)} · 평가금액 ${fmtWonShort(snap.PositionValue)}`}
            />
            <Stat
              label="당일 손익률"
              size="lg"
              value={fmtPercent(snap.DayReturnPercent)}
              tone={signColor(snap.DayReturnPercent)}
              hint={
                snap.BaseAsset
                  ? `직전 거래일 ${fmtWonShort(snap.BaseAsset)} 대비`
                  : '기준선 없음'
              }
            />
            <Stat
              label="평가손익"
              size="lg"
              value={`${fmtWonSigned(evaluated)}원`}
              tone={signColor(evaluated)}
              hint={pricedAt ? `${fmtStamp(pricedAt)} 사이클 가격 기준` : '가격 없음'}
            />
            <Stat
              label="누적 손익 (TWR)"
              size="lg"
              value={fmtPercent(data?.twrReturn)}
              tone={signColor(data?.twrReturn)}
              hint="이체 효과를 뺀 시간가중수익률"
            />

            <Stat
              label="안전 출금 가능액"
              value={`${fmtWon(data?.safeWithdrawable)}원`}
              hint="예수금 − 미체결 매수"
            />
            <Stat
              label="누적 순입금"
              value={`${fmtWonSigned(data?.cumulativeNetFlow)}원`}
            />
            <Stat
              label="누적 손익"
              value={`${fmtWonSigned(cumulativeProfit)}원`}
              tone={signColor(cumulativeProfit)}
            />
            <Stat
              label="보유 종목"
              value={`${holdings.length}`}
              hint={`평가금액 ${fmtWonShort(snap.PositionValue)}`}
            />
          </div>

          {holdings.length === 0 ? (
            <Empty>보유 중인 종목이 없습니다.</Empty>
          ) : (
            <ul className="divide-y divide-ink-800">
              {shown.map((h) => (
                <HoldingRow key={h.PositionId} h={h} />
              ))}
            </ul>
          )}

          {holdings.length > COLLAPSED_ROWS && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="w-full border-t border-ink-800 py-2.5 text-xs text-ink-400 transition-colors hover:bg-ink-850 hover:text-ink-50"
            >
              {expanded
                ? '접기'
                : `더보기 (${holdings.length - COLLAPSED_ROWS}종목)`}
            </button>
          )}
        </>
      )}
    </Panel>
  )
}

function HoldingRow({ h }: { h: Holding }) {
  const ret = h.ReturnPercent
  // 손절가까지 남은 거리 — 지금 손절이 걸리면 얼마를 잃는지가 이 한 줄에 있다
  const toStop =
    h.LastPrice && h.CurrentStopPrice ? h.CurrentStopPrice / h.LastPrice - 1 : null

  return (
    <li className="flex items-center gap-4 px-5 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="truncate text-sm font-medium">{h.Name ?? h.SymbolId}</span>
          <span className="font-mono text-[11px] text-ink-400">{h.SymbolId}</span>
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[11px] text-ink-400">
          <span>평단 {fmtWon(h.AveragePrice)}</span>
          <span>{h.Quantity}주</span>
          <span>{h.HoldingDays ?? '—'}일</span>
          <span>
            손절 {fmtWon(h.CurrentStopPrice)}
            {toStop !== null && (
              <span className="text-ink-700"> ({fmtPercent(toStop, 1)})</span>
            )}
          </span>
          {h.EntryDate && <span className="text-ink-700">{fmtDate(h.EntryDate)} 진입</span>}
        </div>
      </div>
      <div className="text-right">
        <div className={`font-mono text-sm font-semibold ${signColor(ret)}`}>
          {fmtPercent(ret)}
        </div>
        <div className={`font-mono text-[11px] ${signColor(h.ProfitLoss)}`}>
          {fmtWonSigned(h.ProfitLoss)}
        </div>
      </div>
      <div className="hidden w-24 text-right sm:block">
        <div className="font-mono text-sm">{fmtWon(h.LastPrice)}</div>
        <div className="text-[11px] text-ink-400">
          {h.LastPrice ? '현재가' : <Badge tone="warn">가격 없음</Badge>}
        </div>
      </div>
    </li>
  )
}
