import { useState } from 'react'
import type { Notification } from './WarRoomShell'

interface NotificationTrayProps {
  sidebarCollapsed: boolean
  notifications: Notification[]
  onClear: () => void
}

export function NotificationTray({ sidebarCollapsed, notifications, onClear }: NotificationTrayProps) {
  const [expanded, setExpanded] = useState(false)
  const unread = notifications.filter(n => !n.read).length

  return (
    <footer
      className={`fixed bottom-0 right-0 z-40 bg-surface-card border-t border-border-default transition-all duration-200 ${
        sidebarCollapsed ? 'left-0' : 'left-60'
      } ${expanded ? 'h-60' : 'h-12'}`}
    >
      {/* Collapsed bar */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full h-12 px-4 flex items-center justify-between text-sm text-text-secondary hover:text-text-primary transition-colors"
      >
        <div className="flex items-center gap-3">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path
              d="M11.08 5.25C11.08 4.28 10.7 3.35 10 2.65C9.31 1.96 8.38 1.58 7.41 1.58C6.44 1.58 5.51 1.96 4.81 2.65C4.12 3.35 3.75 4.28 3.75 5.25C3.75 9.33 1.92 10.5 1.92 10.5H12.92C12.92 10.5 11.08 9.33 11.08 5.25Z"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="text-xs">Notifications</span>

          <span className={`px-1.5 py-0.5 text-[10px] font-mono rounded ${
            unread > 0
              ? 'bg-accent/20 text-accent'
              : 'bg-surface-input text-text-muted'
          }`}>
            {unread > 0 ? `${unread} new` : '0 pending'}
          </span>
        </div>

        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          className={`transition-transform ${expanded ? 'rotate-180' : ''}`}
        >
          <path d="M3 7.5L6 4.5L9 7.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {/* Expanded panel */}
      {expanded && (
        <div className="px-4 py-2 overflow-y-auto h-48">
          {notifications.length === 0 ? (
            <div className="text-sm text-text-muted flex items-center justify-center h-full">
              No notifications
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-text-muted">{notifications.length} notification{notifications.length !== 1 ? 's' : ''}</span>
                <button
                  onClick={onClear}
                  className="text-xs text-text-muted hover:text-accent transition-colors"
                >
                  Clear all
                </button>
              </div>
              <div className="space-y-1.5">
                {notifications.map(n => (
                  <div
                    key={n.id}
                    className={`text-sm px-3 py-2 rounded border ${
                      n.priority === 'high'
                        ? 'border-red-500/30 bg-red-500/5'
                        : 'border-border-default bg-surface-input/50'
                    }`}
                  >
                    <p className="text-text-primary">{n.message}</p>
                    <p className="text-[10px] text-text-muted mt-0.5 font-mono">
                      {new Date(n.timestamp * 1000).toLocaleTimeString()}
                    </p>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </footer>
  )
}
