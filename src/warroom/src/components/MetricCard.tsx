interface MetricCardProps {
  label: string
  value: string | number
  trend?: {
    direction: 'up' | 'down' | 'flat'
    text: string
  }
  color?: string
  className?: string
}

export function MetricCard({ label, value, trend, className = '' }: MetricCardProps) {
  const trendColor =
    trend?.direction === 'up'
      ? 'text-state-healthy'
      : trend?.direction === 'down'
        ? 'text-state-error'
        : 'text-text-muted'

  const trendIcon =
    trend?.direction === 'up' ? '▲' : trend?.direction === 'down' ? '▼' : '—'

  return (
    <div
      className={`bg-surface-card border border-border-default rounded-lg p-4 ${className}`}
    >
      <p className="text-metric-label uppercase text-text-secondary tracking-wider">
        {label}
      </p>
      <p className="text-metric font-mono text-text-primary mt-1">
        {typeof value === 'number' ? value.toLocaleString() : value}
      </p>
      {trend && (
        <p className={`text-xs mt-1 ${trendColor}`}>
          {trendIcon} {trend.text}
        </p>
      )}
    </div>
  )
}
