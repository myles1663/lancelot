interface ProgressBarProps {
  value: number
  max?: number
  color?: string
  className?: string
}

export function ProgressBar({
  value,
  max = 100,
  color = 'bg-accent-primary',
  className = '',
}: ProgressBarProps) {
  const percentage = Math.min(100, Math.max(0, (value / max) * 100))

  return (
    <div
      className={`w-full h-1 rounded-full bg-surface-input overflow-hidden ${className}`}
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
    >
      <div
        className={`h-full rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${percentage}%` }}
      />
    </div>
  )
}
