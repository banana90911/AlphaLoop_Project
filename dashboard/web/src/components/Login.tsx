/**
 * description:        로그인 화면 (비밀번호 1개)
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:            08-dashboard 8.6
 *                     출입증은 화면 코드가 읽을 수 없는 쿠키에 담긴다. 그래서 여기서
 *                     토큰을 받아 저장하는 코드가 없다 — 서버가 쿠키로 붙여 준다.
 */

import type { FormEvent } from 'react'
import { useState } from 'react'
import { ApiError, login } from '../api'

export function Login({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy || !password) return
    setBusy(true)
    setError(null)
    try {
      await login(password)
      setPassword('')
      onSuccess()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : '서버에 닿지 못했습니다. 연결을 확인하세요.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-full items-center justify-center px-6">
      <form
        onSubmit={submit}
        className="w-full max-w-[320px] rounded-2xl border border-ink-800 bg-ink-900/80 p-7"
      >
        <div className="mb-6">
          <div className="mb-1 flex items-center gap-2">
            <Mark />
            <span className="text-[15px] font-semibold tracking-tight">AlphaLoop</span>
          </div>
          <p className="text-xs text-ink-400">브라우저를 닫거나 12시간이 지나면 다시 넣습니다.</p>
        </div>

        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
          autoComplete="current-password"
          placeholder="비밀번호"
          className="w-full rounded-lg border border-ink-800 bg-ink-950 px-3 py-2.5 text-sm outline-none placeholder:text-ink-700 focus:border-ink-700"
        />

        {error && <p className="mt-2 text-[11px] text-up">{error}</p>}

        <button
          type="submit"
          disabled={busy || !password}
          className="mt-4 w-full rounded-lg bg-ink-50 py-2.5 text-sm font-semibold text-ink-950 transition-opacity disabled:opacity-30"
        >
          {busy ? '확인 중…' : '들어가기'}
        </button>
      </form>
    </main>
  )
}

export const Mark = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
    <path
      d="M2 11.5 5.5 6l3 3.5L13.5 3"
      stroke="var(--color-up)"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <circle cx="13.5" cy="3" r="1.6" fill="var(--color-up)" />
  </svg>
)
