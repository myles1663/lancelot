import { useState, useCallback, useEffect, useRef } from 'react'
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

const STATE_BADGES: Record<string, { label: string; bg: string; text: string }> = {
  spawning: { label: 'Spawning', bg: 'bg-gray-500/10', text: 'text-gray-400' },
  ready: { label: 'Ready', bg: 'bg-blue-500/10', text: 'text-blue-400' },
  executing: { label: 'Executing', bg: 'bg-green-500/10', text: 'text-green-400' },
  paused: { label: 'Paused', bg: 'bg-yellow-500/10', text: 'text-yellow-400' },
  completing: { label: 'Completing', bg: 'bg-purple-500/10', text: 'text-purple-400' },
  collapsed: { label: 'Collapsed', bg: 'bg-red-500/10', text: 'text-red-400' },
}

const COLLAPSE_BADGES: Record<string, { label: string; color: string }> = {
  completed: { label: 'Completed', color: 'text-green-400' },
  operator_kill: { label: 'Killed', color: 'text-red-400' },
  operator_kill_all: { label: 'Kill All', color: 'text-red-400' },
  soul_violation: { label: 'Soul Violation', color: 'text-orange-400' },
  governance_denied: { label: 'Denied', color: 'text-orange-400' },
  timeout: { label: 'Timeout', color: 'text-yellow-400' },
  error: { label: 'Error', color: 'text-red-400' },
  max_actions_exceeded: { label: 'Max Actions', color: 'text-yellow-400' },
}

function StateBadge({ state }: { state: string }) {
  const cfg = STATE_BADGES[state] || { label: state, bg: 'bg-gray-500/10', text: 'text-gray-400' }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${cfg.bg} ${cfg.text}`}>
      {cfg.label}
    </span>
  )
}

export function HiveAgentMesh() {
  usePageTitle('HIVE Agent Mesh')
  const [status, setStatus] = useState<HiveStatus | null>(null)
  const [roster, setRoster] = useState<HiveRoster | null>(null)
  const [activeTab, setActiveTab] = useState<'active' | 'history'>('active')
  const [error, setError] = useState<string | null>(null)
  const [goalInput, setGoalInput] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Intervention dialog state
  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogType, setDialogType] = useState<'pause' | 'kill' | 'modify'>('kill')
  const [dialogAgentId, setDialogAgentId] = useState('')

  const loadData = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([getHiveStatus(), getHiveRoster()])
      setStatus(s)
      setRoster(r)
      setError(null)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load HIVE data'
      if (!msg.includes('503')) setError(msg)
    }
  }, [])

  // Poll on interval
  const timerRef = useRef<ReturnType<typeof setInterval>>()
  useEffect(() => {
    loadData()
    timerRef.current = setInterval(loadData, 3000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [loadData])

  const openDialog = (type: 'pause' | 'kill' | 'modify', agentId: string) => {
    setDialogType(type)
    setDialogAgentId(agentId)
    setDialogOpen(true)
  }

  const handleDialogConfirm = async (reason: string) => {
    try {
      if (dialogType === 'pause') {
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

  const handleKillAll = async () => {
    const reason = prompt('Reason for killing all agents:')
    if (!reason?.trim()) return
    try {
      await killAll(reason)
      loadData()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Kill all failed')
    }
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

  if (!status?.enabled) {
    return (
      <div className="p-6">
        <h2 className="text-xl font-semibold text-text-primary mb-2">HIVE Agent Mesh</h2>
        <div className="bg-surface-card border border-border-default rounded-lg p-6 text-center text-text-muted">
          HIVE is disabled. Enable <code className="text-xs bg-surface-input px-1 rounded">FEATURE_HIVE</code> to activate.
        </div>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-text-primary">HIVE Agent Mesh</h2>
        <div className="flex items-center gap-3">
          <span className="text-sm text-text-muted">
            Status: <span className="text-text-primary font-medium">{status?.status || 'unknown'}</span>
          </span>
          <button
            onClick={handleKillAll}
            className="px-3 py-1.5 text-xs bg-red-600/10 text-red-400 border border-red-600/30 rounded hover:bg-red-600/20 transition-colors"
          >
            Kill All
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm px-4 py-2 rounded">
          {error}
          <button onClick={() => setError(null)} className="ml-2 underline">dismiss</button>
        </div>
      )}

      {/* Architect Status Bar */}
      <div className="bg-surface-card border border-border-default rounded-lg p-4 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <div>
            <span className="text-xs text-text-muted">Active Agents</span>
            <p className="text-2xl font-bold text-text-primary">
              {status?.active_agents ?? 0}
              <span className="text-sm text-text-muted font-normal">/{status?.max_agents ?? 10}</span>
            </p>
          </div>
          {status?.goal && (
            <div>
              <span className="text-xs text-text-muted">Current Goal</span>
              <p className="text-sm text-text-primary truncate max-w-md">{status.goal}</p>
            </div>
          )}
          {status?.plan && status.plan.subtask_count > 0 && (
            <div>
              <span className="text-xs text-text-muted">Subtasks</span>
              <p className="text-sm text-text-primary">{status.plan.subtask_count}</p>
            </div>
          )}
        </div>

        {/* Task submission */}
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={goalInput}
            onChange={(e) => setGoalInput(e.target.value)}
            placeholder="Enter a task goal..."
            className="w-64 px-3 py-1.5 bg-surface-input border border-border-default rounded text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary"
            onKeyDown={(e) => e.key === 'Enter' && handleSubmitTask()}
          />
          <button
            onClick={handleSubmitTask}
            disabled={submitting || !goalInput.trim()}
            className="px-4 py-1.5 text-sm bg-accent-primary text-white rounded hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
          >
            {submitting ? 'Submitting...' : 'Submit'}
          </button>
        </div>
      </div>

      {/* Tab Selector */}
      <div className="flex gap-1 border-b border-border-default">
        <button
          onClick={() => setActiveTab('active')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'active'
              ? 'border-accent-primary text-accent-primary'
              : 'border-transparent text-text-muted hover:text-text-primary'
          }`}
        >
          Active ({roster?.active.length ?? 0})
        </button>
        <button
          onClick={() => setActiveTab('history')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'history'
              ? 'border-accent-primary text-accent-primary'
              : 'border-transparent text-text-muted hover:text-text-primary'
          }`}
        >
          History ({roster?.archived.length ?? 0})
        </button>
      </div>

      {/* Agent Table */}
      <div className="bg-surface-card border border-border-default rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border-default bg-surface-card-elevated">
              <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Agent</th>
              <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Task</th>
              <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">State</th>
              <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Actions</th>
              <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Control</th>
              {activeTab === 'history' && (
                <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Collapse</th>
              )}
              <th className="text-right px-4 py-2 text-text-muted font-medium text-xs">Controls</th>
            </tr>
          </thead>
          <tbody>
            {(activeTab === 'active' ? roster?.active : roster?.archived)?.map((agent) => (
              <AgentRow
                key={agent.agent_id}
                agent={agent}
                showCollapse={activeTab === 'history'}
                onPause={() => openDialog('pause', agent.agent_id)}
                onKill={() => openDialog('kill', agent.agent_id)}
                onResume={() => handleResume(agent.agent_id)}
              />
            ))}
            {((activeTab === 'active' ? roster?.active : roster?.archived)?.length ?? 0) === 0 && (
              <tr>
                <td colSpan={activeTab === 'history' ? 7 : 6} className="px-4 py-8 text-center text-text-muted">
                  {activeTab === 'active' ? 'No active agents' : 'No archived agents'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Intervention Dialog */}
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

  const collapseCfg = agent.collapse_reason
    ? COLLAPSE_BADGES[agent.collapse_reason] || { label: agent.collapse_reason, color: 'text-gray-400' }
    : null

  return (
    <tr className="border-b border-border-default/50 hover:bg-surface-card-elevated/50 transition-colors">
      <td className="px-4 py-2">
        <code className="text-xs text-text-muted">{agent.agent_id.slice(0, 8)}</code>
      </td>
      <td className="px-4 py-2 text-text-primary truncate max-w-xs">
        {agent.task_description || '-'}
      </td>
      <td className="px-4 py-2">
        <StateBadge state={agent.state} />
      </td>
      <td className="px-4 py-2 text-text-secondary font-mono text-xs">
        {agent.action_count}
      </td>
      <td className="px-4 py-2 text-text-muted text-xs">
        {agent.control_method}
      </td>
      {showCollapse && (
        <td className="px-4 py-2">
          {collapseCfg && (
            <span className={`text-xs font-medium ${collapseCfg.color}`}>
              {collapseCfg.label}
            </span>
          )}
        </td>
      )}
      <td className="px-4 py-2 text-right">
        {isActive && (
          <div className="flex items-center justify-end gap-1">
            {isPaused ? (
              <button
                onClick={onResume}
                className="px-2 py-1 text-xs bg-green-600/10 text-green-400 border border-green-600/30 rounded hover:bg-green-600/20 transition-colors"
              >
                Resume
              </button>
            ) : (
              <button
                onClick={onPause}
                className="px-2 py-1 text-xs bg-yellow-600/10 text-yellow-400 border border-yellow-600/30 rounded hover:bg-yellow-600/20 transition-colors"
              >
                Pause
              </button>
            )}
            <button
              onClick={onKill}
              className="px-2 py-1 text-xs bg-red-600/10 text-red-400 border border-red-600/30 rounded hover:bg-red-600/20 transition-colors"
            >
              Kill
            </button>
          </div>
        )}
      </td>
    </tr>
  )
}
