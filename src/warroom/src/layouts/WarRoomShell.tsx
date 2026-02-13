import { useState, useCallback } from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { NotificationTray } from './NotificationTray'
import { Toast } from '@/components/Toast'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import { useWebSocket, WsEvent } from '@/hooks/useWebSocket'

export interface Notification {
  id: string
  message: string
  priority: 'normal' | 'high'
  timestamp: number
  read: boolean
}

export function WarRoomShell() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [toasts, setToasts] = useState<Notification[]>([])

  const handleWsEvent = useCallback((event: WsEvent) => {
    if (event.type === 'warroom_notification') {
      const notif: Notification = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        message: (event.payload.message as string) || 'New notification',
        priority: (event.payload.priority as 'normal' | 'high') || 'normal',
        timestamp: event.timestamp || Date.now() / 1000,
        read: false,
      }
      setNotifications(prev => [notif, ...prev].slice(0, 50))
      setToasts(prev => [...prev, notif])
    }
  }, [])

  useWebSocket({
    url: '/ws/warroom',
    enabled: true,
    onMessage: handleWsEvent,
  })

  const dismissToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const clearNotifications = useCallback(() => {
    setNotifications([])
  }, [])

  useKeyboardShortcuts()

  return (
    <div className="min-h-screen bg-surface-bg text-text-primary">
      <Header
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebar={() => setSidebarCollapsed(false)}
      />

      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(true)}
      />

      {/* Primary content area */}
      <main
        className={`pt-14 pb-12 transition-all duration-200 ${
          sidebarCollapsed ? 'pl-0' : 'pl-60'
        }`}
      >
        <div className="p-6 max-w-[1600px] mx-auto">
          <Outlet />
        </div>
      </main>

      {/* Toast notifications — top right */}
      <div className="fixed top-16 right-4 z-50 flex flex-col gap-2">
        {toasts.map(t => (
          <Toast
            key={t.id}
            id={t.id}
            message={t.message}
            priority={t.priority}
            onDismiss={dismissToast}
          />
        ))}
      </div>

      <NotificationTray
        sidebarCollapsed={sidebarCollapsed}
        notifications={notifications}
        onClear={clearNotifications}
      />
    </div>
  )
}
