/**
 * description:        60초 주기 재조회 훅
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:            페이지 전체를 다시 그리지 않는다(8.2). 첫 조회만 로딩 상태를 내고,
 *                     이후 갱신은 조용히 값만 바꿔 React가 바뀐 노드만 다시 칠하게 둔다.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'

export const REFRESH_MS = 60_000

export type Polled<T> = {
  data: T | null
  error: string | null   // 보여줄 데이터가 없을 때의 실패 — 화면을 덮는다
  stale: boolean         // 데이터는 있는데 갱신만 실패 — 조용히 다음 주기를 기다린다
  loading: boolean       // 첫 조회 중일 때만 true
  updatedAt: number | null
  refresh: () => void
}

/**
 * `fetcher`가 바뀌면(= 필터가 바뀌면) 즉시 다시 조회하고 타이머도 다시 잡는다.
 * 호출부에서 fetcher를 useCallback으로 감싸야 매 렌더마다 재조회하지 않는다.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  { enabled = true, onUnauthorized }: { enabled?: boolean; onUnauthorized?: () => void } = {},
): Polled<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stale, setStale] = useState(false)
  const [loading, setLoading] = useState(true)
  // 실패 처리에서 최신 data를 봐야 하는데 클로저 값은 낡을 수 있다
  const latest = useRef<T | null>(null)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  // 늦게 온 응답이 최신 응답을 덮어쓰지 않게 세대 번호로 거른다
  const generation = useRef(0)
  const unauthorized = useRef(onUnauthorized)
  unauthorized.current = onUnauthorized

  const run = useCallback(async () => {
    const mine = ++generation.current
    try {
      const next = await fetcher()
      if (mine !== generation.current) return
      latest.current = next
      setData(next)
      setError(null)
      setStale(false)
      setUpdatedAt(Date.now())
    } catch (e) {
      if (mine !== generation.current) return
      if (e instanceof ApiError && e.status === 401) {
        unauthorized.current?.()
        return
      }
      // 이미 보여줄 데이터가 있으면 갱신 실패로 화면을 덮지 않는다. Vercel↔Funnel 구간이
      // 간헐적으로 끊겨(2026-09-07 실측) 멀쩡한 화면이 몇 초 만에 오류로 바뀌곤 했다.
      // 갱신이 계속 실패하면 헤더의 "N초 전 갱신"이 늘어나는 것으로 드러난다.
      if (latest.current !== null) {
        setStale(true)
        return
      }
      setError(e instanceof Error ? e.message : '알 수 없는 오류')
    } finally {
      if (mine === generation.current) setLoading(false)
    }
  }, [fetcher])

  useEffect(() => {
    if (!enabled) return
    void run()
    const id = setInterval(() => void run(), REFRESH_MS)
    return () => clearInterval(id)
  }, [run, enabled])

  return { data, error, stale, loading, updatedAt, refresh: run }
}
