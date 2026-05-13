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
  const inFlightRef = useRef(false)
  const pendingRefetchRef = useRef(false)
  const mountedRef = useRef(true)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const doFetch = useCallback(async () => {
    if (inFlightRef.current) {
      pendingRefetchRef.current = true
      return
    }

    inFlightRef.current = true
    try {
      do {
        pendingRefetchRef.current = false
        try {
          const result = await fetcherRef.current()
          if (mountedRef.current) {
            setData(result)
            setError(null)
          }
        } catch (err) {
          if (mountedRef.current) {
            setError(err instanceof Error ? err : new Error(String(err)))
          }
        }
      } while (pendingRefetchRef.current && mountedRef.current)
    } finally {
      inFlightRef.current = false
      if (mountedRef.current) {
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return
    }

    setLoading(true)
    doFetch()
    timerRef.current = setInterval(doFetch, interval)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [doFetch, interval, enabled])

  return { data, error, loading, refetch: doFetch }
}
