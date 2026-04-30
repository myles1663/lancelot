import { useEffect } from 'react'

interface ToastProps {
  id: string
  message: string
  priority?: 'normal' | 'high'
  onDismiss: (id: string) => void
  duration?: number
}

export function Toast({ id, message, priority = 'normal', onDismiss, duration = 5000 }: ToastProps) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(id), duration)
    return () => clearTimeout(timer)
  }, [id, onDismiss, duration])

  const borderColor = priority === 'high' ? 'border-l-red-500' : 'border-l-accent'

  return (
    <div
      className={`bg-surface-card border border-border-default ${borderColor} border-l-4 rounded-lg shadow-lg px-4 py-3 max-w-sm animate-slide-in`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-xs text-text-muted font-mono mb-1">
            {priority === 'high' ? 'PRIORITY' : 'NOTIFICATION'}
          </p>
          <p className="text-sm text-text-primary break-words">{message}</p>
        </div>
        <button
          onClick={() => onDismiss(id)}
          className="text-text-muted hover:text-text-primary transition-colors flex-shrink-0"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M10.5 3.5L3.5 10.5M3.5 3.5L10.5 10.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </div>
    </div>
  )
}
