interface EmptyStateProps {
  title: string
  description: string
  icon?: string
  className?: string
}

export function EmptyState({
  title,
  description,
  icon = '📭',
  className = '',
}: EmptyStateProps) {
  return (
    <div className={`flex flex-col items-center justify-center py-12 ${className}`}>
      <span className="text-4xl mb-4" role="img" aria-hidden="true">
        {icon}
      </span>
      <h3 className="text-base font-medium text-text-primary">{title}</h3>
      <p className="text-sm text-text-muted mt-1 text-center max-w-sm">
        {description}
      </p>
    </div>
  )
}
