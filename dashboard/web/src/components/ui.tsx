/**
 * description:        화면 공통 조각 (패널·토글·상태 표시)
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:
 */

import type { ReactNode } from 'react'

export function Panel({
  title,
  subtitle,
  right,
  children,
}: {
  title: string
  subtitle?: string
  right?: ReactNode
  children: ReactNode
}) {
  return (
    // 배경을 불투명하게 둔다 — 반투명 + backdrop-blur는 이 안의 캔버스(차트)까지
    // 합성 레이어로 밀어 넣어 저해상도로 래스터화되게 만든다
    <section className="rounded-2xl border border-ink-800 bg-ink-900">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-ink-800 px-5 py-4">
        <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
        {subtitle && <p className="text-xs text-ink-400">{subtitle}</p>}
        <div className="ml-auto flex items-center gap-2">{right}</div>
      </header>
      {children}
    </section>
  )
}

/** 값 하나를 보여주는 칸. `hint`는 그 숫자를 어떻게 읽어야 하는지 한 줄로 알려준다. */
export function Stat({
  label,
  value,
  hint,
  tone = 'text-ink-50',
  size = 'md',
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: string
  size?: 'md' | 'lg'
}) {
  return (
    <div className="flex flex-col gap-1 px-5 py-4">
      <span className="text-xs text-ink-400">{label}</span>
      <span
        className={`font-mono font-semibold tracking-tight ${tone} ${
          // 폰에서는 총자본 일곱 자리가 두 줄로 접힌다 — 좁은 화면에서만 한 단계 줄인다
          size === 'lg' ? 'text-[21px] sm:text-[26px]' : 'text-[16px] sm:text-[19px]'
        }`}
      >
        {value}
      </span>
      {hint && <span className="text-[11px] leading-snug text-ink-400">{hint}</span>}
    </div>
  )
}

export function Toggle<T extends string>({
  options,
  value,
  onChange,
  size = 'md',
}: {
  /** `short`가 있으면 좁은 화면에서 그쪽을 쓴다 — 긴 라벨은 폰에서 두 줄로 접힌다 */
  options: { value: T; label: string; short?: string; title?: string }[]
  value: T
  onChange: (v: T) => void
  size?: 'sm' | 'md'
}) {
  return (
    <div className="inline-flex rounded-lg border border-ink-800 bg-ink-950/60 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          title={o.title}
          onClick={() => onChange(o.value)}
          className={`rounded-[6px] font-medium whitespace-nowrap transition-colors ${
            size === 'sm' ? 'px-2.5 py-1 text-[11px]' : 'px-3 py-1.5 text-xs'
          } ${
            value === o.value
              ? 'bg-ink-700 text-ink-50'
              : 'text-ink-400 hover:bg-ink-850 hover:text-ink-200'
          }`}
        >
          {o.short ? (
            <>
              <span className="sm:hidden">{o.short}</span>
              <span className="hidden sm:inline">{o.label}</span>
            </>
          ) : (
            o.label
          )}
        </button>
      ))}
    </div>
  )
}

/** 여러 개를 동시에 켜고 끄는 칩(벤치마크 겹쳐 그리기 등) */
export function Chip({
  active,
  color,
  onClick,
  children,
}: {
  active: boolean
  color?: string
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
        active
          ? 'border-ink-700 bg-ink-850 text-ink-50'
          : 'border-ink-800 text-ink-400 hover:text-ink-200'
      }`}
    >
      {color && (
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: active ? color : 'var(--color-ink-700)' }}
        />
      )}
      {children}
    </button>
  )
}

const BADGE_TONES = {
  neutral: 'border-ink-700 bg-ink-850 text-ink-200',
  up: 'border-up/40 bg-up/10 text-up',
  down: 'border-down/40 bg-down/10 text-down',
  buy: 'border-buy/40 bg-buy/10 text-buy',
  sell: 'border-sell/40 bg-sell/10 text-sell',
  flow: 'border-ink-700 bg-ink-800/60 text-ink-200',
  warn: 'border-warn/40 bg-warn/10 text-warn',
} as const

export function Badge({
  tone = 'neutral',
  children,
}: {
  tone?: keyof typeof BADGE_TONES
  children: ReactNode
}) {
  return (
    <span
      className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap ${BADGE_TONES[tone]}`}
    >
      {children}
    </span>
  )
}

/** 첫 조회 중 자리 잡아 두는 회색 막대 — 값이 들어올 때 화면이 튀지 않게 */
export const Skeleton = ({ className = '' }: { className?: string }) => (
  <div className={`animate-pulse rounded bg-ink-800 ${className}`} />
)

export const Empty = ({ children }: { children: ReactNode }) => (
  <p className="px-5 py-10 text-center text-sm text-ink-400">{children}</p>
)

export const ErrorLine = ({ message }: { message: string }) => (
  <p className="mx-5 my-4 rounded-lg border border-up/30 bg-up/5 px-3 py-2 text-xs text-up">
    {message}
  </p>
)

/** 근거 펼침 안에서 쓰는 이름:값 한 줄 */
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-ink-800/60 py-1.5 last:border-0">
      <span className="text-[11px] whitespace-nowrap text-ink-400">{label}</span>
      <span className="font-mono text-xs text-ink-50">{children}</span>
    </div>
  )
}

export function FieldGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <h4 className="mb-1 text-[11px] font-semibold tracking-wide text-ink-200 uppercase">
        {title}
      </h4>
      <div>{children}</div>
    </div>
  )
}
