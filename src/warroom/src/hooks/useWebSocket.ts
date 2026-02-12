import { useEffect, useRef, useState, useCallback } from 'react'

export interface WsEvent {
  type: string
  payload: Record<string, unknown>
  timestamp: number
}

interface UseWebSocketOptions {
  url: string
  enabled?: boolean
  reconnectInterval?: number
  onMessage?: (event: WsEvent) => void
}

type WsStatus = 'connecting' | 'connected' | 'disconnected' | 'error'

export function useWebSocket({
  url,
  enabled = true,
  reconnectInterval = 3000,
  onMessage,
}: UseWebSocketOptions) {
  const [status, setStatus] = useState<WsStatus>('disconnected')
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  const connect = useCallback(() => {
    if (!enabled) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const fullUrl = url.startsWith('ws') ? url : `${protocol}//${window.location.host}${url}`

    setStatus('connecting')
    const ws = new WebSocket(fullUrl)
    wsRef.current = ws

    ws.onopen = () => setStatus('connected')

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as WsEvent
        onMessageRef.current?.(data)
      } catch {
        // non-JSON message, ignore
      }
    }

    ws.onerror = () => setStatus('error')

    ws.onclose = () => {
      setStatus('disconnected')
      wsRef.current = null
      if (enabled) {
        reconnectTimer.current = setTimeout(connect, reconnectInterval)
      }
    }
  }, [url, enabled, reconnectInterval])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const send = useCallback((data: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
    }
  }, [])

  return { status, send }
}
