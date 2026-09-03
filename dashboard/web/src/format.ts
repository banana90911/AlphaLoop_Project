/**
 * description:        숫자·시각 표기 (KST 고정)
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:            서버는 시각을 UTC로 저장하고 거래일은 KST 날짜다.
 *                     화면은 무조건 KST로 읽는다 — 해외에서 열어도 장 시간과 어긋나지 않게.
 */

const KST = 'Asia/Seoul'

const won = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
const wonSigned = new Intl.NumberFormat('ko-KR', {
  maximumFractionDigits: 0,
  signDisplay: 'always',
})

/** 1,234,567 */
export const fmtWon = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : won.format(Math.round(v))

/** +1,234,567 / −1,234,567 */
export const fmtWonSigned = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : wonSigned.format(Math.round(v)).replace('-', '−')

/** 억·만 단위로 접어 KPI 카드에서 자리를 아낀다 */
export function fmtWonShort(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const abs = Math.abs(v)
  const sign = v < 0 ? '−' : ''
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(abs >= 1e9 ? 0 : 2)}억`
  if (abs >= 1e4) return `${sign}${won.format(Math.round(abs / 1e4))}만`
  return `${sign}${won.format(Math.round(abs))}`
}

/** 비율(0.0123) → +1.23% */
export function fmtPercent(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const s = (v * 100).toFixed(digits)
  return `${v > 0 ? '+' : ''}${s.replace('-', '−')}%`
}

export const fmtNumber = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined ? '—' : v.toFixed(digits)

/** 백분위(0~1) → 87 */
export const fmtPercentile = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : Math.round(v * 100).toString()

/** 값의 방향 → 색. 오르면 빨강, 내리면 파랑, 0이면 무채색(08-dashboard 8.4) */
export function signColor(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return 'text-ink-200'
  return v > 0 ? 'text-up' : 'text-down'
}

const dtf = (opts: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat('ko-KR', { timeZone: KST, ...opts })

const dateFmt = dtf({ year: 'numeric', month: '2-digit', day: '2-digit' })
const timeFmt = dtf({ hour: '2-digit', minute: '2-digit', hour12: false })
const stampFmt = dtf({
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/** '2026-09-04' 또는 ISO 시각 → '2026.09.04' */
export function fmtDate(v: string | null | undefined): string {
  if (!v) return '—'
  // 날짜만 온 값은 이미 KST 거래일이다 — 시간대 변환을 태우면 하루가 밀린다
  if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return v.replaceAll('-', '.')
  return dateFmt.format(new Date(v)).replaceAll(' ', '').replace(/\.$/, '')
}

/** ISO 시각 → '14:30' (KST) */
export const fmtTime = (v: string | null | undefined) =>
  v ? timeFmt.format(new Date(v)) : '—'

/** ISO 시각 → '09.04 14:30' (KST) */
export const fmtStamp = (v: string | null | undefined) =>
  v ? stampFmt.format(new Date(v)).replaceAll(' ', ' ') : '—'

/** 몇 분 전인지 — 마지막 갱신이 얼마나 낡았는지 한눈에 보이게 */
export function fmtAgo(ms: number): string {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000))
  if (s < 60) return `${s}초 전`
  if (s < 3600) return `${Math.floor(s / 60)}분 전`
  return `${Math.floor(s / 3600)}시간 전`
}

/** 날짜 문자열(YYYY-MM-DD)이면 KST 자정, ISO 시각이면 그대로 → epoch ms */
export const toMs = (v: string) =>
  /^\d{4}-\d{2}-\d{2}$/.test(v) ? Date.parse(`${v}T00:00:00+09:00`) : Date.parse(v)

// en-CA가 YYYY-MM-DD를 그대로 내준다 — 서버가 받는 형식과 같아 변환이 한 번으로 끝난다
const isoDateFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: KST,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

/** 오늘로부터 n일 전의 KST 날짜(YYYY-MM-DD) */
export const kstDaysAgo = (days: number) =>
  isoDateFmt.format(new Date(Date.now() - days * 86400_000))
