interface SkeletonProps {
  className?: string
  variant?: 'text' | 'card' | 'metric' | 'row'
}

const VARIANT_CLASSES: Record<string, string> = {
  text: 'h-4 w-full',
  card: 'h-24 w-full rounded-lg',
  metric: 'h-20 w-full rounded-lg',
  row: 'h-12 w-full rounded-md',
}

export function Skeleton({ className = '', variant = 'text' }: SkeletonProps) {
  return (
    <div
      className={`animate-shimmer bg-gradient-to-r from-surface-card via-surface-card-elevated to-surface-card bg-[length:200%_100%] rounded ${VARIANT_CLASSES[variant]} ${className}`}
    />
  )
}
