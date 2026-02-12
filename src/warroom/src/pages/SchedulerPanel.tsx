import { usePolling } from '@/hooks'
import { fetchHealthReady } from '@/api'
import { StatusDot, EmptyState } from '@/components'

export function SchedulerPanel() {
  const { data } = usePolling({ fetcher: fetchHealthReady, interval: 10000 })

  const schedulerRunning = data?.scheduler_running ?? false
  const lastTick = data?.last_scheduler_tick_at

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Scheduler</h2>

      {/* Status */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          Scheduler Status
        </h3>
        <div className="flex items-center gap-6">
          <StatusDot state={schedulerRunning ? 'healthy' : 'inactive'} label={schedulerRunning ? 'Running' : 'Stopped'} />
          {lastTick && (
            <span className="text-xs font-mono text-text-muted">
              Last tick: {new Date(lastTick).toLocaleString()}
            </span>
          )}
        </div>
      </section>

      {/* Jobs — WR-25 will add /api/scheduler/jobs */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          Scheduled Jobs
        </h3>
        <EmptyState
          title="Job Management Coming Soon"
          description="The scheduler jobs API will be available after WR-25 backend implementation. Jobs are currently managed via config files."
          icon="&#9200;"
        />
      </section>
    </div>
  )
}
