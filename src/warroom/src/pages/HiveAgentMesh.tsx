import { useState, useCallback, useEffect, useRef } from 'react'
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  CirclePause,
  History,
  ListChecks,
  Network,
  OctagonX,
  PlayCircle,
  Send,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { usePageTitle } from '@/hooks'
import {
  getHiveStatus,
  getHiveRoster,
  pauseAgent,
  resumeAgent,
  killAgent,
  killAll,
  submitTask,
  type HiveStatus,
  type HiveRoster,
  type HiveAgent,
} from '@/api/hive'
import { InterventionDialog } from '@/components/InterventionDialog'
import { formatTimestamp } from '@/utils/dateFormat'

const STATE_BADGES: Record<string, { label: string; className: string }> = {
  spawning: {
    label: 'Spawning',
    className: 'border-border-default bg-surface-input text-text-muted',
  },
  ready: {
    label: 'Ready',
    className: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  },
  executing: {
    label: 'Executing',
    className: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  },
  paused: {
    label: 'Paused',
    className: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  },
  completing: {
    label: 'Completing',
    className: 'border-purple-500/30 bg-purple-500/10 text-purple-400',
  },
  collapsed: {
    label: 'Collapsed',
    className: 'border-state-error/30 bg-state-error/10 text-state-error',
  },
}

const COLLAPSE_BADGES: Record<string, { label: string; className: string }> = {
  completed: {
    label: 'Completed',
    className: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  },
  operator_kill: {
    label: 'Killed',
    className: 'border-state-error/30 bg-state-error/10 text-state-error',
  },
  operator_kill_all: {
    label: 'Kill All',
    className: 'border-state-error/30 bg-state-error/10 text-state-error',
  },
  soul_violation: {
    label: 'Soul Violation',
    className: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  },
  governance_denied: {
    label: 'Denied',
    className: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  },
  timeout: {
    label: 'Timeout',
    className: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  },
  error: {
    label: 'Error',
    className: 'border-state-error/30 bg-state-error/10 text-state-error',
  },
  max_actions_exceeded: {
    label: 'Max Actions',
    className: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  },
}

type MeshTileTone = 'accent' | 'healthy' | 'warning' | 'error' | 'muted'

const meshTileToneClass: Record<MeshTileTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  error: 'border-state-error/30 bg-state-error/10 text-state-error',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function StateBadge({ state }: { state: string }) {
  const cfg = STATE_BADGES[state] || {
    label: state,
    className: 'border-border-default bg-surface-input text-text-muted',
  }
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${cfg.className}`}>
      {cfg.label}
    </span>
  )
}

function CollapseBadge({ reason }: { reason?: string }) {
  if (!reason) return <span className="text-xs text-text-muted">-</span>
  const cfg = COLLAPSE_BADGES[reason] || {
    label: reason,
    className: 'border-border-default bg-surface-input text-text-muted',
  }
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${cfg.className}`}>
      {cfg.label}
    </span>
  )
}

function MeshTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: MeshTileTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${meshTileToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 break-words text-2xl font-semibold leading-tight text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

function formatGoal(goal?: string) {
  if (!goal) return 'No active HIVE goal.'
  return goal.length > 140 ? `${goal.slice(0, 137)}...` : goal
}

function formatAgentId(agentId: string) {
  return agentId.length > 12 ? agentId.slice(0, 12) : agentId
}

export function HiveAgentMesh() {
  usePageTitle('HIVE Agent Mesh')
  const [status, setStatus] = useState<HiveStatus | null>(null)
  const [roster, setRoster] = useState<HiveRoster | null>(null)
  const [activeTab, setActiveTab] = useState<'active' | 'history'>('active')
  const [error, setError] = useState<string | null>(null)
  const [goalInput, setGoalInput] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [manualSubmitOpen, setManualSubmitOpen] = useState(false)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogType, setDialogType] = useState<'pause' | 'kill' | 'modify'>('kill')
  const [dialogAgentId, setDialogAgentId] = useState('')
  const [dialogScope, setDialogScope] = useState<'agent' | 'all'>('agent')
  const inFlightRef = useRef(false)

  const loadData = useCallback(async () => {
    if (inFlightRef.current) return

    inFlightRef.current = true
    try {
      const [s, r] = await Promise.all([getHiveStatus(), getHiveRoster()])
      setStatus(s)
      setRoster(r)
      setError(null)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load HIVE data'
      if (!msg.includes('503')) setError(msg)
    } finally {
      inFlightRef.current = false
    }
  }, [])

  const timerRef = useRef<ReturnType<typeof setInterval>>()
  useEffect(() => {
    loadData()
    timerRef.current = setInterval(loadData, 3000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [loadData])

  const openDialog = (type: 'pause' | 'kill' | 'modify', agentId: string, scope: 'agent' | 'all' = 'agent') => {
    setDialogType(type)
    setDialogAgentId(agentId)
    setDialogScope(scope)
    setDialogOpen(true)
  }

  const handleDialogConfirm = async (reason: string) => {
    try {
      if (dialogScope === 'all') {
        await killAll(reason)
      } else if (dialogType === 'pause') {
        await pauseAgent(dialogAgentId, reason)
      } else if (dialogType === 'kill') {
        await killAgent(dialogAgentId, reason)
      }
      setDialogOpen(false)
      loadData()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Action failed')
    }
  }

  const handleKillAll = () => {
    openDialog('kill', 'all active HIVE agents', 'all')
  }

  const handleSubmitTask = async () => {
    if (!goalInput.trim()) return
    setSubmitting(true)
    try {
      await submitTask(goalInput)
      setGoalInput('')
      loadData()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Task submission failed')
    } finally {
      setSubmitting(false)
    }
  }

  const handleResume = async (agentId: string) => {
    try {
      await resumeAgent(agentId)
      loadData()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Resume failed')
    }
  }

  const activeAgents = roster?.active ?? []
  const archivedAgents = roster?.archived ?? []
  const executingCount = activeAgents.filter((agent) => agent.state === 'executing').length
  const pausedCount = activeAgents.filter((agent) => agent.state === 'paused').length
  const completedCount = archivedAgents.filter((agent) => agent.collapse_reason === 'completed').length
  const interventionCount = [...activeAgents, ...archivedAgents].reduce(
    (total, agent) => total + (agent.interventions?.length ?? 0),
    0,
  )
  const maxAgents = status?.max_agents ?? 10
  const activeCount = status?.active_agents ?? activeAgents.length
  const capacityTone: MeshTileTone = activeCount >= maxAgents ? 'warning' : activeCount > 0 ? 'healthy' : 'muted'
  const visibleAgents = activeTab === 'active' ? activeAgents : archivedAgents

  if (status && !status.enabled) {
    return (
      <div className="space-y-6">
        <div className="rounded-lg border border-border-default bg-surface-card p-5">
          <div className="flex items-center gap-2">
            <Network className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <div className="text-[11px] font-semibold uppercase tracking-wider text-accent-primary">
              Agent Mesh
            </div>
          </div>
          <h2 className="mt-2 text-2xl font-semibold text-text-primary">HIVE Agent Mesh</h2>
          <p className="mt-2 text-sm leading-6 text-text-muted">
            Governed sub-agent execution is currently disabled.
          </p>
        </div>
        <div className="rounded-lg border border-state-warning/30 bg-state-warning/10 p-6 text-sm text-state-warning">
          Enable <code className="rounded bg-surface-input px-1 py-0.5 text-xs">FEATURE_HIVE</code> to activate the HIVE runtime.
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border-default bg-surface-card p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2">
              <Network className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <div className="text-[11px] font-semibold uppercase tracking-wider text-accent-primary">
                Agent Mesh
              </div>
            </div>
            <h2 className="mt-2 text-2xl font-semibold text-text-primary">HIVE Agent Mesh</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Decompose governed work into bounded sub-agents, monitor execution pressure, and intervene before drift becomes risk.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono text-text-muted sm:flex sm:flex-wrap">
            <span className={`rounded border px-2 py-1 ${status?.enabled ? 'border-state-healthy/40 bg-state-healthy/10 text-state-healthy' : 'border-border-default bg-surface-input'}`}>
              {status?.enabled ? 'enabled' : 'loading'}
            </span>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1">
              {status?.status ?? 'unknown'}
            </span>
            <span className={`rounded border px-2 py-1 ${pausedCount > 0 ? 'border-state-warning/40 bg-state-warning/10 text-state-warning' : 'border-border-default bg-surface-input'}`}>
              paused {pausedCount}
            </span>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-5">
          <MeshTile
            label="Capacity"
            value={`${activeCount}/${maxAgents}`}
            detail="Active agents against configured maximum."
            tone={capacityTone}
          />
          <MeshTile
            label="Executing"
            value={executingCount}
            detail="Agents currently performing work."
            tone={executingCount > 0 ? 'healthy' : 'muted'}
          />
          <MeshTile
            label="Planned Subtasks"
            value={status?.plan?.subtask_count ?? 0}
            detail={`${status?.plan_revision_count ?? 0} plan revisions recorded.`}
            tone={(status?.plan?.subtask_count ?? 0) > 0 ? 'accent' : 'muted'}
          />
          <MeshTile
            label="Archived"
            value={archivedAgents.length}
            detail={`${completedCount} completed agents in history.`}
          />
          <MeshTile
            label="Interventions"
            value={interventionCount}
            detail="Operator pause, kill, or modify events."
            tone={interventionCount > 0 ? 'warning' : 'muted'}
          />
        </div>
      </div>

      {error && (
        <div className="flex items-start justify-between gap-3 rounded-lg border border-state-error/30 bg-state-error/10 px-4 py-3 text-sm text-state-error">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
          <button onClick={() => setError(null)} className="text-xs underline">
            dismiss
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <div className="rounded-lg border border-border-default bg-surface-card p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-state-healthy" aria-hidden="true" />
            <h3 className="text-sm font-semibold text-text-primary">Delegation Intake</h3>
          </div>
          <p className="mt-1 text-xs leading-5 text-text-muted">
            HIVE work should normally be created by Lancelot from governed workflows, not launched directly by an operator.
          </p>
          <div className="mt-4 grid grid-cols-1 gap-3 text-xs text-text-muted sm:grid-cols-3">
            <div className="rounded border border-border-default bg-surface-card-elevated px-3 py-2">
              <div className="font-medium text-text-secondary">Normal intake</div>
              <div className="mt-1 leading-5">Command Center, scheduler, and governed workflows.</div>
            </div>
            <div className="rounded border border-border-default bg-surface-card-elevated px-3 py-2">
              <div className="font-medium text-text-secondary">Lancelot decision</div>
              <div className="mt-1 leading-5">Policy decides when HIVE decomposition is appropriate.</div>
            </div>
            <div className="rounded border border-border-default bg-surface-card-elevated px-3 py-2">
              <div className="font-medium text-text-secondary">Operator role</div>
              <div className="mt-1 leading-5">Monitor agents, pause, resume, kill, and review receipts.</div>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setManualSubmitOpen((open) => !open)}
            className="mt-4 inline-flex items-center gap-2 rounded border border-border-default bg-surface-input px-3 py-2 text-xs font-medium text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
          >
            <Send className="h-3.5 w-3.5" aria-hidden="true" />
            {manualSubmitOpen ? 'Hide Manual HIVE Task' : 'Show Manual HIVE Task'}
          </button>

          {manualSubmitOpen && (
            <div className="mt-4 rounded-lg border border-state-warning/30 bg-state-warning/10 p-4">
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-state-warning" aria-hidden="true" />
                <div>
                  <h4 className="text-sm font-semibold text-text-primary">Manual HIVE Task</h4>
                  <p className="mt-1 text-xs leading-5 text-text-muted">
                    Use this for diagnostics or live smoke tests when you intentionally want to bypass normal Lancelot intake.
                  </p>
                </div>
              </div>
              <div className="mt-4 flex flex-col gap-3 lg:flex-row">
                <input
                  type="text"
                  value={goalInput}
                  onChange={(e) => setGoalInput(e.target.value)}
                  placeholder="Enter a diagnostic HIVE goal..."
                  className="min-w-0 flex-1 rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-primary"
                  onKeyDown={(e) => e.key === 'Enter' && handleSubmitTask()}
                />
                <button
                  onClick={handleSubmitTask}
                  disabled={submitting || !goalInput.trim()}
                  className="inline-flex items-center justify-center gap-2 rounded bg-accent-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {submitting ? (
                    <>
                      <Activity className="h-4 w-4 animate-spin" aria-hidden="true" />
                      Submitting...
                    </>
                  ) : (
                    <>
                      <Send className="h-4 w-4" aria-hidden="true" />
                      Submit Manual Task
                    </>
                  )}
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-border-default bg-surface-card p-5">
          <div className="flex items-center gap-2">
            <ListChecks className="h-4 w-4 text-state-healthy" aria-hidden="true" />
            <h3 className="text-sm font-semibold text-text-primary">Current Goal</h3>
          </div>
          <p className="mt-2 text-sm leading-6 text-text-secondary">{formatGoal(status?.goal)}</p>
          {status?.quest_id && (
            <p className="mt-3 w-fit rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
              quest {status.quest_id}
            </p>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-border-default bg-surface-card">
        <div className="flex flex-col gap-4 border-b border-border-default px-5 py-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Users className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <h3 className="text-sm font-semibold text-text-primary">Agent Roster</h3>
            </div>
            <p className="mt-1 text-xs leading-5 text-text-muted">
              Inspect active agents, review archived outcomes, and apply operator controls.
            </p>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="inline-flex rounded-lg border border-border-default bg-surface-input p-1">
              <button
                onClick={() => setActiveTab('active')}
                className={`inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  activeTab === 'active'
                    ? 'bg-accent-primary/15 text-accent-primary'
                    : 'text-text-muted hover:text-text-primary'
                }`}
              >
                <Activity className="h-3.5 w-3.5" aria-hidden="true" />
                Active ({activeAgents.length})
              </button>
              <button
                onClick={() => setActiveTab('history')}
                className={`inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  activeTab === 'history'
                    ? 'bg-accent-primary/15 text-accent-primary'
                    : 'text-text-muted hover:text-text-primary'
                }`}
              >
                <History className="h-3.5 w-3.5" aria-hidden="true" />
                History ({archivedAgents.length})
              </button>
            </div>
            <button
              onClick={handleKillAll}
              disabled={activeAgents.length === 0}
              className="inline-flex items-center justify-center gap-2 rounded border border-state-error/30 bg-state-error/10 px-3 py-2 text-xs font-medium text-state-error transition-colors hover:bg-state-error/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <OctagonX className="h-3.5 w-3.5" aria-hidden="true" />
              Kill All
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-sm">
            <thead>
              <tr className="border-b border-border-default bg-surface-card-elevated text-left text-xs text-text-muted">
                <th className="px-4 py-2 font-medium">Agent</th>
                <th className="px-4 py-2 font-medium">Task</th>
                <th className="px-4 py-2 font-medium">State</th>
                <th className="px-4 py-2 font-medium">Actions</th>
                <th className="px-4 py-2 font-medium">Control</th>
                {activeTab === 'history' && (
                  <th className="px-4 py-2 font-medium">Collapse</th>
                )}
                <th className="px-4 py-2 text-right font-medium">Controls</th>
              </tr>
            </thead>
            <tbody>
              {visibleAgents.map((agent) => (
                <AgentRow
                  key={agent.agent_id}
                  agent={agent}
                  showCollapse={activeTab === 'history'}
                  onPause={() => openDialog('pause', agent.agent_id)}
                  onKill={() => openDialog('kill', agent.agent_id)}
                  onResume={() => handleResume(agent.agent_id)}
                />
              ))}
              {visibleAgents.length === 0 && (
                <tr>
                  <td colSpan={activeTab === 'history' ? 7 : 6} className="px-4 py-10 text-center text-text-muted">
                    {activeTab === 'active' ? 'No active agents' : 'No archived agents'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-state-healthy" aria-hidden="true" />
            <h4 className="text-sm font-semibold text-text-primary">Scoped Souls</h4>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Sub-agents inherit narrower operating constraints than the parent Lancelot runtime.
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <CirclePause className="h-4 w-4 text-state-warning" aria-hidden="true" />
            <h4 className="text-sm font-semibold text-text-primary">Operator Intervention</h4>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Pause, resume, and kill controls are explicit operator actions with required reasons.
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <h4 className="text-sm font-semibold text-text-primary">Receipt Trail</h4>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            HIVE task, agent, and intervention events are visible through the receipt trail.
          </p>
        </div>
      </div>

      <InterventionDialog
        open={dialogOpen}
        type={dialogType}
        agentId={dialogAgentId}
        onConfirm={handleDialogConfirm}
        onCancel={() => setDialogOpen(false)}
      />
    </div>
  )
}

function AgentRow({
  agent,
  showCollapse,
  onPause,
  onKill,
  onResume,
}: {
  agent: HiveAgent
  showCollapse: boolean
  onPause: () => void
  onKill: () => void
  onResume: () => void
}) {
  const isActive = ['executing', 'paused', 'ready'].includes(agent.state)
  const isPaused = agent.state === 'paused'

  return (
    <tr className="border-b border-border-default/50 transition-colors hover:bg-surface-card-elevated/40">
      <td className="px-4 py-3 align-top">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-text-muted" aria-hidden="true" />
          <div>
            <code className="block text-xs text-text-secondary" title={agent.agent_id}>
              {formatAgentId(agent.agent_id)}
            </code>
            {agent.quest_id && (
              <span className="mt-1 block text-[10px] font-mono text-text-muted">
                quest {formatAgentId(agent.quest_id)}
              </span>
            )}
          </div>
        </div>
      </td>
      <td className="max-w-[360px] px-4 py-3 align-top">
        <div className="truncate text-text-primary" title={agent.task_description || '-'}>
          {agent.task_description || '-'}
        </div>
        <div className="mt-1 text-[10px] font-mono text-text-muted">
          created {formatTimestamp(agent.created_at)}
        </div>
      </td>
      <td className="px-4 py-3 align-top">
        <StateBadge state={agent.state} />
      </td>
      <td className="px-4 py-3 align-top font-mono text-xs text-text-secondary">
        {agent.action_count}
      </td>
      <td className="px-4 py-3 align-top">
        <span className="rounded border border-border-default bg-surface-input px-2 py-0.5 text-xs font-mono text-text-muted">
          {agent.control_method}
        </span>
      </td>
      {showCollapse && (
        <td className="px-4 py-3 align-top">
          <CollapseBadge reason={agent.collapse_reason} />
          {agent.collapse_message && (
            <div className="mt-1 max-w-[260px] truncate text-[10px] text-text-muted" title={agent.collapse_message}>
              {agent.collapse_message}
            </div>
          )}
        </td>
      )}
      <td className="px-4 py-3 text-right align-top">
        {isActive ? (
          <div className="flex items-center justify-end gap-1.5">
            {isPaused ? (
              <button
                onClick={onResume}
                className="inline-flex h-8 w-8 items-center justify-center rounded border border-state-healthy/30 bg-state-healthy/10 text-state-healthy transition-colors hover:bg-state-healthy/20"
                title="Resume agent"
              >
                <PlayCircle className="h-4 w-4" aria-hidden="true" />
              </button>
            ) : (
              <button
                onClick={onPause}
                className="inline-flex h-8 w-8 items-center justify-center rounded border border-state-warning/30 bg-state-warning/10 text-state-warning transition-colors hover:bg-state-warning/20"
                title="Pause agent"
              >
                <CirclePause className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
            <button
              onClick={onKill}
              className="inline-flex h-8 w-8 items-center justify-center rounded border border-state-error/30 bg-state-error/10 text-state-error transition-colors hover:bg-state-error/20"
              title="Kill agent"
            >
              <OctagonX className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        ) : (
          <span className="text-xs text-text-muted">-</span>
        )}
      </td>
    </tr>
  )
}
