import { Skeleton } from './Skeleton'

interface PageLoaderProps {
  metrics?: number
  sections?: number
}

export function PageLoader({ metrics = 4, sections = 2 }: PageLoaderProps) {
  return (
    <div className="space-y-6">
      <Skeleton variant="text" className="h-6 w-48" />
      {metrics > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Array.from({ length: metrics }).map((_, i) => (
            <Skeleton key={i} variant="metric" />
          ))}
        </div>
      )}
      {Array.from({ length: sections }).map((_, i) => (
        <Skeleton key={i} variant="card" className="h-48" />
      ))}
    </div>
  )
}
