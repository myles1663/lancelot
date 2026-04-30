type SystemState = 'healthy' | 'degraded' | 'error' | 'inactive'

const STATE_CONFIG: Record<SystemState, { color: string; label: string }> = {
  healthy: { color: 'bg-state-healthy', label: 'Healthy' },
  degraded: { color: 'bg-state-degraded', label: 'Degraded' },
  error: { color: 'bg-state-error', label: 'Error' },
  inactive: { color: 'bg-state-inactive', label: 'Inactive' },
}

interface StatusDotProps {
  state: SystemState
  label?: string
  pulse?: boolean
  className?: string
}

export function StatusDot({ state, label, pulse = false, className = '' }: StatusDotProps) {
  const config = STATE_CONFIG[state]
  const displayLabel = label ?? config.label

  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <span
        className={`w-2 h-2 rounded-full ${config.color} ${pulse ? 'animate-pulse' : ''}`}
      />
      <span className="text-xs text-text-secondary">{displayLabel}</span>
    </span>
  )
}

export type { SystemState }
