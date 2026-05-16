import { useState, useCallback, useEffect } from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { NotificationTray } from './NotificationTray'
import { Toast } from '@/components/Toast'
import { UpdateBanner } from '@/components/UpdateBanner'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import { useWebSocket, WsEvent } from '@/hooks/useWebSocket'
import { LiveEventsProvider, useLiveEvents } from '@/contexts/LiveEventsContext'
import { onWarRoomNotification, type WarRoomNotificationPriority } from '@/utils/notifications'

export interface Notification {
  id: string
  message: string
  priority: 'normal' | 'high'
  timestamp: number
  read: boolean
}

// Inner shell — has access to LiveEventsContext via useLiveEvents()
function WarRoomShellInner() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.innerWidth < 1024
  })
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [toasts, setToasts] = useState<Notification[]>([])
  const { handleLiveEvent } = useLiveEvents()

  const enqueueNotification = useCallback((
    message: string,
    priority: WarRoomNotificationPriority = 'normal',
  ) => {
    const notif: Notification = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      message,
      priority,
      timestamp: Date.now() / 1000,
      read: false,
    }
    setNotifications(prev => [notif, ...prev].slice(0, 50))
    setToasts(prev => [...prev, notif])
  }, [])

  const handleWsEvent = useCallback((event: WsEvent) => {
    if (event.type === 'warroom_notification') {
      const message = typeof event.payload.message === 'string'
        ? event.payload.message
        : 'New notification'
      const priority = event.payload.priority === 'high' ? 'high' : 'normal'
      enqueueNotification(message, priority)
    }

    // Route live execution/progress events to LiveEventsContext
    if (
      event.type.startsWith('toolflow.') ||
      event.type.startsWith('actioncard_') ||
      event.type === 'chat.progress' ||
      event.type.startsWith('chat.run_')
    ) {
      handleLiveEvent(event)
    }
  }, [enqueueNotification, handleLiveEvent])

  useEffect(() => onWarRoomNotification(({ message, priority }) => {
    enqueueNotification(message, priority)
  }), [enqueueNotification])

  useEffect(() => {
    const handleResize = () => {
      setSidebarCollapsed(window.innerWidth < 1024)
    }
    handleResize()
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
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
    <div className="fixed inset-0 h-screen h-[100dvh] overflow-hidden bg-surface-bg text-text-primary">
      <Header
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebar={() => setSidebarCollapsed(false)}
        notificationCount={notifications.filter(n => !n.read).length}
      />

      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(true)}
      />

      {/* Primary content area */}
      <main
        className={`fixed left-0 right-0 top-14 bottom-12 overflow-y-auto overscroll-contain transition-[left] duration-200 ${
          sidebarCollapsed ? 'lg:left-0' : 'lg:left-60'
        }`}
      >
        <div className="p-4 sm:p-6 max-w-[1600px] mx-auto">
          <UpdateBanner />
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

// Outer shell — wraps inner with LiveEventsProvider so context is available
export function WarRoomShell() {
  return (
    <LiveEventsProvider>
      <WarRoomShellInner />
    </LiveEventsProvider>
  )
}
