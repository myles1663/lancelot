import { useEffect, useRef, useState, useCallback } from 'react'

interface UsePollingOptions<T> {
  fetcher: () => Promise<T>
  interval: number
  enabled?: boolean
}

interface UsePollingResult<T> {
  data: T | null
  error: Error | null
  loading: boolean
  refetch: () => void
}

export function usePolling<T>({
  fetcher,
  interval,
  enabled = true,
}: UsePollingOptions<T>): UsePollingResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const timerRef = useRef<ReturnType<typeof setInterval>>()
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const doFetch = useCallback(async () => {
    try {
      const result = await fetcherRef.current()
      setData(result)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    doFetch()
    timerRef.current = setInterval(doFetch, interval)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [doFetch, interval, enabled])

  return { data, error, loading, refetch: doFetch }
}
