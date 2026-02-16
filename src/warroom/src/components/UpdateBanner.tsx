import { useState, useCallback } from 'react'
import { usePolling } from '@/hooks/usePolling'
import { fetchUpdateStatus, checkForUpdate, dismissUpdate } from '@/api/updates'
import type { UpdateStatusResponse } from '@/types/api'

interface SeverityStyle { bg: string; border: string; badge: string; badgeText: string }

const SEVERITY_STYLES: Record<string, SeverityStyle> = {
  info: {
    bg: 'bg-blue-500/10',
    border: 'border-blue-500/30',
    badge: 'bg-blue-500/20 text-blue-400',
    badgeText: 'Info',
  },
  recommended: {
    bg: 'bg-accent/10',
    border: 'border-accent/30',
    badge: 'bg-accent/20 text-accent',
    badgeText: 'Recommended',
  },
  important: {
    bg: 'bg-yellow-500/10',
    border: 'border-yellow-500/30',
    badge: 'bg-yellow-500/20 text-yellow-400',
    badgeText: 'Important',
  },
  critical: {
    bg: 'bg-red-500/10',
    border: 'border-red-500/30',
    badge: 'bg-red-500/20 text-red-400',
    badgeText: 'Critical',
  },
}

export function UpdateBanner() {
  const [checking, setChecking] = useState(false)

  const { data, refetch } = usePolling<UpdateStatusResponse>({
    fetcher: fetchUpdateStatus,
    interval: 5 * 60 * 1000, // 5 minutes
  })

  const handleCheckNow = useCallback(async () => {
    setChecking(true)
    try {
      await checkForUpdate()
      refetch()
    } finally {
      setChecking(false)
    }
  }, [refetch])

  const handleDismiss = useCallback(async () => {
    await dismissUpdate()
    refetch()
  }, [refetch])

  // Don't render if no data, no update available, or banner dismissed
  if (!data || !data.update_available || !data.show_banner) return null

  const severity = data.severity || 'info'
  const style: SeverityStyle = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES['info']!
  const canDismiss = severity === 'info' || severity === 'recommended'

  return (
    <div
      className={`mb-4 rounded-lg border ${style.bg} ${style.border} p-4`}
    >
      <div className="flex items-start justify-between gap-4">
        {/* Left: version info + message */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-1">
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${style.badge}`}>
              {style.badgeText}
            </span>
            <span className="text-sm font-medium text-text-primary">
              Update Available: {data.current_version} → {data.latest_version}
            </span>
          </div>

          {data.message && (
            <p className="text-sm text-text-secondary mt-1">{data.message}</p>
          )}
        </div>

        {/* Right: actions */}
        <div className="flex items-center gap-2 shrink-0">
          {data.changelog_url && (
            <a
              href={data.changelog_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-accent hover:text-accent/80 underline underline-offset-2"
            >
              View Changelog
            </a>
          )}

          <button
            onClick={handleCheckNow}
            disabled={checking}
            className="px-3 py-1 text-xs rounded bg-surface-card hover:bg-surface-card/80 text-text-secondary border border-border transition-colors disabled:opacity-50"
          >
            {checking ? 'Checking...' : 'Check Now'}
          </button>

          {canDismiss && (
            <button
              onClick={handleDismiss}
              className="px-3 py-1 text-xs rounded bg-surface-card hover:bg-surface-card/80 text-text-secondary border border-border transition-colors"
            >
              Dismiss
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
