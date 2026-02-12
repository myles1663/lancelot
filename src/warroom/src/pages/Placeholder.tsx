import { EmptyState } from '@/components'

interface PlaceholderPageProps {
  title: string
  description?: string
}

export function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">{title}</h2>
      <EmptyState
        title="Coming Soon"
        description={description || `The ${title} panel will be implemented in an upcoming phase.`}
        icon="🚧"
      />
    </div>
  )
}
