import { useCallback, useMemo, useState } from 'react'
import { checkpointWorkItem, fetchActiveWork, resumeWorkItem } from '@/api'
import { StatusDot } from '@/components'
import { usePolling } from '@/hooks'
import { useLiveEvents } from '@/contexts/LiveEventsContext'
import { formatTimeOnly } from '@/utils/dateFormat'
import type { ActiveWorkItem, ActiveWorkStatus } from '@/types/api'

function formatLabel(value: string): string {
  return (value || 'unknown').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function statusState(status: ActiveWorkStatus): 'healthy' | 'error' | 'degraded' | 'inactive' {
  if (status === 'active' || status === 'checkpointed') return 'healthy'
  if (status === 'blocked') return 'degraded'
  if (status === 'failed' || status === 'cancelled') return 'error'
  return 'inactive'
}

function workTimestamp(item: ActiveWorkItem): string {
  if (Number.isNaN(Date.parse(item.updated_at))) return ''
  return formatTimeOnly(item.updated_at)
}

function workHeadline(item: ActiveWorkItem): string {
  if (item.blocker) return item.blocker
  if (item.next_action) return item.next_action
  if (item.current_step) return item.current_step
  return item.objective || item.quest_id
}

function canResume(item: ActiveWorkItem): boolean {
  return item.status === 'blocked' && Boolean(item.last_chat_run_id || item.quest_id)
}

function ActiveWorkRow({
  item,
  actionPending,
  onCheckpoint,
  onResume,
}: {
  item: ActiveWorkItem
  actionPending: boolean
  onCheckpoint: (questId: string) => void
  onResume: (questId: string) => void
}) {
  const headline = workHeadline(item)
  const updatedAt = workTimestamp(item)

  return (
    <div className="rounded-md border border-border-default bg-surface-card-elevated px-3 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <StatusDot state={statusState(item.status)} />
            <p className="text-xs font-medium text-text-primary truncate">
              {item.objective || item.quest_id}
            </p>
          </div>
          <p className="mt-1 text-[10px] leading-relaxed text-text-muted break-words">
            {headline}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[9px] font-mono uppercase tracking-wider text-text-muted">
            <span>{formatLabel(item.status)}</span>
            <span>{formatLabel(item.phase)}</span>
            <span>{item.quest_id.slice(0, 8)}</span>
            {updatedAt && <span>{updatedAt}</span>}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          <button
            type="button"
            onClick={() => onCheckpoint(item.quest_id)}
            disabled={actionPending}
            className="px-2 py-1 text-[10px] font-medium rounded-md border border-border-default text-text-secondary hover:text-accent-primary hover:border-accent-primary/60 disabled:opacity-50 disabled:cursor-not-allowed"
            title="Create a durable checkpoint for this work item"
          >
            {actionPending ? 'Saving' : 'Checkpoint'}
          </button>
          {canResume(item) && (
            <button
              type="button"
              onClick={() => onResume(item.quest_id)}
              disabled={actionPending}
              className="px-2 py-1 text-[10px] font-medium rounded-md bg-accent-primary text-white hover:bg-accent-primary/80 disabled:opacity-50 disabled:cursor-not-allowed"
              title="Resume the retained governed chat run"
            >
              {actionPending ? 'Queuing' : 'Resume'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export function ActiveWorkPanel() {
  const { trackChatRun } = useLiveEvents()
  const fetcher = useCallback(() => fetchActiveWork(5), [])
  const { data, error, loading, refetch } = usePolling({ fetcher, interval: 10000 })
  const [actionPendingQuestId, setActionPendingQuestId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string>('')

  const items = useMemo(() => data?.items ?? [], [data])

  const handleCheckpoint = useCallback(
    async (questId: string) => {
      if (actionPendingQuestId) return
      setActionPendingQuestId(questId)
      setActionError('')
      try {
        await checkpointWorkItem(questId, 'operator_checkpoint')
        refetch()
      } catch (err) {
        setActionError(err instanceof Error ? err.message : 'Unable to checkpoint work')
      } finally {
        setActionPendingQuestId(null)
      }
    },
    [actionPendingQuestId, refetch],
  )

  const handleResume = useCallback(
    async (questId: string) => {
      if (actionPendingQuestId) return
      setActionPendingQuestId(questId)
      setActionError('')
      try {
        const result = await resumeWorkItem(questId)
        trackChatRun(result.run)
        refetch()
      } catch (err) {
        setActionError(err instanceof Error ? err.message : 'Unable to resume work')
      } finally {
        setActionPendingQuestId(null)
      }
    },
    [actionPendingQuestId, refetch, trackChatRun],
  )

  return (
    <section className="bg-surface-card border border-border-default rounded-lg p-4">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Active Work</h3>
        <button
          type="button"
          onClick={refetch}
          className="text-[10px] font-medium text-text-muted hover:text-text-primary"
          title="Refresh active work"
        >
          Refresh
        </button>
      </div>

      {error && (
        <p className="text-xs text-state-error mb-2">
          Active work unavailable: {error.message}
        </p>
      )}
      {actionError && (
        <p className="text-xs text-state-error mb-2">
          {actionError}
        </p>
      )}

      {loading && items.length === 0 ? (
        <p className="text-sm text-text-muted">Loading active work...</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-text-muted">No active work</p>
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <ActiveWorkRow
              key={item.quest_id}
              item={item}
              actionPending={actionPendingQuestId === item.quest_id}
              onCheckpoint={handleCheckpoint}
              onResume={handleResume}
            />
          ))}
        </div>
      )}
    </section>
  )
}
