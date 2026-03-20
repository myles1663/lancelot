import { useState, useCallback } from 'react'
import { usePolling, usePageTitle } from '@/hooks'
import { MetricCard, EmptyState } from '@/components'
import { formatRelativeTime, formatTimestamp } from '@/utils/dateFormat'
import {
  fetchA2AStatus,
  fetchRemoteAgents,
  fetchRemoteAgent,
  fetchOwnAgentCard,
  fetchA2AReceipts,
  registerRemoteAgent,
  revokeRemoteAgent,
  verifyAgentCard,
  regenerateAgentCard,
  delegateTask,
  type A2AStatus,
  type AgentListResponse,
  type RemoteAgent,
  type LancelotAgentCard,
} from '@/api/a2a'

// ── Constants ─────────────────────────────────────────────────

const TRUST_COLORS = [
  'text-emerald-400 bg-emerald-500/15',
  'text-blue-400 bg-blue-500/15',
  'text-amber-400 bg-amber-500/15',
  'text-red-400 bg-red-500/15',
]

const TRUST_LABELS = ['T0 · Core', 'T1 · Trusted', 'T2 · Default', 'T3 · Untrusted']

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  suspended: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  revoked: 'bg-red-500/15 text-red-400 border-red-500/30',
}

const DIRECTION_STYLES: Record<string, string> = {
  inbound: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  outbound: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  both: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
}

const FRAMEWORK_STYLES: Record<string, string> = {
  crewai: 'bg-emerald-500/15 text-emerald-400',
  langchain: 'bg-orange-500/15 text-orange-400',
  google_adk: 'bg-blue-500/15 text-blue-400',
  lancelot: 'bg-indigo-500/15 text-indigo-400',
  unknown: 'bg-zinc-500/15 text-zinc-400',
}

const CARD_STYLES: Record<string, string> = {
  verified: 'bg-emerald-500/15 text-emerald-400',
  stale: 'bg-amber-500/15 text-amber-400',
  unverified: 'bg-red-500/15 text-red-400',
}

const OUTCOME_STYLES: Record<string, string> = {
  completed: 'text-emerald-400',
  failed: 'text-red-400',
  canceled: 'text-amber-400',
}

type TabId = 'registry' | 'card' | 'activity'

// ── Badges ────────────────────────────────────────────────────

function Badge({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono border ${className}`}>
      {children}
    </span>
  )
}

function TrustBadge({ tier, label }: { tier: number; label: string }) {
  const style = TRUST_COLORS[tier] ?? TRUST_COLORS[2]
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${style}`}>
      {label} T{tier}
    </span>
  )
}

// ── Register Dialog ───────────────────────────────────────────

function RegisterDialog({ onClose, onRegistered }: { onClose: () => void; onRegistered: () => void }) {
  const [agentId, setAgentId] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [cardUrl, setCardUrl] = useState('')
  const [framework, setFramework] = useState('unknown')
  const [direction, setDirection] = useState('outbound')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async () => {
    if (!agentId.trim() || !displayName.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      await registerRemoteAgent({
        agent_id: agentId.trim(),
        display_name: displayName.trim(),
        agent_card_url: cardUrl.trim() || undefined,
        agent_framework: framework,
        direction,
      })
      onRegistered()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Registration failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-surface-card border border-border-default rounded-lg p-6 w-full max-w-lg shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-text-primary mb-4">Register Remote Agent</h3>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-text-muted block mb-1">Agent ID *</label>
              <input type="text" value={agentId} onChange={(e) => setAgentId(e.target.value)}
                placeholder="crew-research-01"
                className="w-full bg-surface-input border border-border-default rounded px-3 py-2 text-sm font-mono text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary" />
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Display Name *</label>
              <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Research Agent"
                className="w-full bg-surface-input border border-border-default rounded px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary" />
            </div>
          </div>

          <div>
            <label className="text-xs text-text-muted block mb-1">Agent Card URL</label>
            <input type="text" value={cardUrl} onChange={(e) => setCardUrl(e.target.value)}
              placeholder="https://agent.example.com/.well-known/agent.json"
              className="w-full bg-surface-input border border-border-default rounded px-3 py-2 text-sm font-mono text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary" />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-text-muted block mb-1">Framework</label>
              <select value={framework} onChange={(e) => setFramework(e.target.value)}
                className="w-full bg-surface-input border border-border-default rounded px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-1 focus:ring-accent-primary">
                <option value="unknown">Unknown</option>
                <option value="crewai">CrewAI</option>
                <option value="langchain">LangChain</option>
                <option value="google_adk">Google ADK</option>
                <option value="lancelot">Lancelot</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Direction</label>
              <select value={direction} onChange={(e) => setDirection(e.target.value)}
                className="w-full bg-surface-input border border-border-default rounded px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-1 focus:ring-accent-primary">
                <option value="outbound">Outbound</option>
                <option value="inbound">Inbound</option>
                <option value="both">Both</option>
              </select>
            </div>
          </div>
        </div>

        {error && <p className="text-sm text-red-400 mt-3">{error}</p>}

        <div className="flex items-center justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-4 py-2 text-sm text-text-muted hover:text-text-primary transition-colors">
            Cancel
          </button>
          <button onClick={handleSubmit} disabled={submitting || !agentId.trim() || !displayName.trim()}
            className="px-4 py-2 text-sm font-medium rounded bg-accent-primary text-white hover:bg-accent-primary/90 disabled:opacity-50 transition-colors">
            {submitting ? 'Registering...' : 'Register Agent'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Delegate Dialog ───────────────────────────────────────────

function DelegateDialog({ agent, onClose }: { agent: RemoteAgent; onClose: () => void }) {
  const [content, setContent] = useState('')
  const [taskType, setTaskType] = useState('general')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleDelegate = async () => {
    if (!content.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await delegateTask({
        target_agent_id: agent.agent_id,
        content: content.trim(),
        task_type: taskType,
      })
      setResult(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delegation failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-surface-card border border-border-default rounded-lg p-6 w-full max-w-lg shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-text-primary mb-1">Delegate Task</h3>
        <p className="text-sm text-text-muted mb-4">
          Send to <span className="text-text-primary font-medium">{agent.display_name}</span>
          <span className="text-text-muted font-mono text-xs ml-1">({agent.agent_id})</span>
        </p>

        {!result ? (
          <>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-text-muted block mb-1">Task Content *</label>
                <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={4}
                  placeholder="Describe the task to delegate..."
                  className="w-full bg-surface-input border border-border-default rounded px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary resize-none" />
              </div>
              <div>
                <label className="text-xs text-text-muted block mb-1">Task Type</label>
                <select value={taskType} onChange={(e) => setTaskType(e.target.value)}
                  className="w-full bg-surface-input border border-border-default rounded px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-1 focus:ring-accent-primary">
                  <option value="general">General</option>
                  <option value="research">Research</option>
                  <option value="analysis">Analysis</option>
                  <option value="code_review">Code Review</option>
                </select>
              </div>
            </div>

            {error && <p className="text-sm text-red-400 mt-3">{error}</p>}

            <div className="flex items-center justify-end gap-2 mt-5">
              <button onClick={onClose} className="px-4 py-2 text-sm text-text-muted hover:text-text-primary transition-colors">Cancel</button>
              <button onClick={handleDelegate} disabled={submitting || !content.trim()}
                className="px-4 py-2 text-sm font-medium rounded bg-amber-600 text-white hover:bg-amber-500 disabled:opacity-50 transition-colors">
                {submitting ? 'Delegating...' : 'Delegate'}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="bg-surface-input border border-border-default rounded p-3 text-xs font-mono text-text-secondary whitespace-pre-wrap max-h-64 overflow-y-auto">
              {JSON.stringify(result, null, 2)}
            </div>
            <div className="flex justify-end mt-4">
              <button onClick={onClose} className="px-4 py-2 text-sm font-medium rounded bg-surface-card-elevated text-text-primary hover:bg-surface-input transition-colors">Close</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Agent Detail Panel ────────────────────────────────────────

function AgentDetail({
  agent,
  onRefresh,
  onDelegate,
}: {
  agent: RemoteAgent
  onRefresh: () => void
  onDelegate: () => void
}) {
  const [verifying, setVerifying] = useState(false)
  const [revoking, setRevoking] = useState(false)

  const handleVerify = async () => {
    setVerifying(true)
    try { await verifyAgentCard(agent.agent_id); onRefresh() } catch { /* */ }
    finally { setVerifying(false) }
  }

  const handleRevoke = async () => {
    if (!confirm(`Revoke agent "${agent.display_name}"? This will block all A2A communication.`)) return
    setRevoking(true)
    try { await revokeRemoteAgent(agent.agent_id); onRefresh() } catch { /* */ }
    finally { setRevoking(false) }
  }

  const successRate = agent.interaction_count > 0
    ? Math.round((agent.success_count / agent.interaction_count) * 100)
    : 0

  return (
    <div className="bg-surface-card border border-border-default rounded-lg overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-border-default">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-base font-semibold text-text-primary">{agent.display_name}</h3>
          <Badge className={STATUS_STYLES[agent.status] || STATUS_STYLES.active}>
            {agent.status.toUpperCase()}
          </Badge>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-mono text-text-muted">{agent.agent_id}</span>
          <Badge className={DIRECTION_STYLES[agent.direction] || DIRECTION_STYLES.outbound}>
            {agent.direction.toUpperCase()}
          </Badge>
          <Badge className={FRAMEWORK_STYLES[agent.agent_framework] || FRAMEWORK_STYLES.unknown}>
            {agent.agent_framework.toUpperCase()}
          </Badge>
          <Badge className={CARD_STYLES[agent.card_status] || CARD_STYLES.unverified}>
            CARD: {agent.card_status.toUpperCase()}
          </Badge>
          {agent.auto_registered && (
            <span className="text-[10px] text-text-muted italic">auto-registered</span>
          )}
        </div>
      </div>

      {/* Trust Tiers */}
      <div className="p-4 border-b border-border-default">
        <h4 className="text-xs font-semibold text-text-muted uppercase mb-3">Trust Tiers</h4>
        <div className="grid grid-cols-2 gap-3">
          <div className="bg-surface-input rounded-lg p-3">
            <span className="text-[10px] text-text-muted block mb-1">INBOUND</span>
            <TrustBadge tier={agent.inbound_trust_tier} label="IN" />
            <p className="text-[10px] text-text-muted mt-1">{TRUST_LABELS[agent.inbound_trust_tier]}</p>
          </div>
          <div className="bg-surface-input rounded-lg p-3">
            <span className="text-[10px] text-text-muted block mb-1">OUTBOUND</span>
            <TrustBadge tier={agent.outbound_trust_tier} label="OUT" />
            <p className="text-[10px] text-text-muted mt-1">{TRUST_LABELS[agent.outbound_trust_tier]}</p>
          </div>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="p-4 border-b border-border-default">
        <h4 className="text-xs font-semibold text-text-muted uppercase mb-3">Interaction History</h4>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <span className="text-[10px] text-text-muted block">Total</span>
            <span className="text-lg font-bold text-text-primary font-mono">{agent.interaction_count}</span>
          </div>
          <div>
            <span className="text-[10px] text-text-muted block">Success</span>
            <span className="text-lg font-bold text-emerald-400 font-mono">{agent.success_count}</span>
          </div>
          <div>
            <span className="text-[10px] text-text-muted block">Success Rate</span>
            <span className={`text-lg font-bold font-mono ${successRate >= 80 ? 'text-emerald-400' : successRate >= 50 ? 'text-amber-400' : 'text-red-400'}`}>
              {successRate}%
            </span>
          </div>
        </div>

        {/* Progress bar */}
        {agent.interaction_count > 0 && (
          <div className="mt-3 h-1.5 bg-surface-input rounded-full overflow-hidden">
            <div className="h-full bg-emerald-500 rounded-full transition-all" style={{ width: `${successRate}%` }} />
          </div>
        )}
      </div>

      {/* Metadata */}
      <div className="p-4 border-b border-border-default">
        <h4 className="text-xs font-semibold text-text-muted uppercase mb-3">Details</h4>
        <div className="grid grid-cols-2 gap-y-2 text-xs">
          <div>
            <span className="text-text-muted">Auth Type</span>
            <p className="text-text-primary">{agent.auth_type || 'none'}</p>
          </div>
          <div>
            <span className="text-text-muted">Kill Switch</span>
            <p className="text-text-primary font-mono text-[11px]">{agent.kill_switch_id}</p>
          </div>
          <div>
            <span className="text-text-muted">Last Interaction</span>
            <p className="text-text-primary">{agent.last_interaction ? formatRelativeTime(agent.last_interaction) : '—'}</p>
          </div>
          <div>
            <span className="text-text-muted">Last Outcome</span>
            <p className={`font-medium ${OUTCOME_STYLES[agent.last_outcome] || 'text-text-muted'}`}>
              {agent.last_outcome || '—'}
            </p>
          </div>
          <div>
            <span className="text-text-muted">Registered</span>
            <p className="text-text-primary">{agent.registered_at ? formatTimestamp(agent.registered_at) : '—'}</p>
          </div>
          <div>
            <span className="text-text-muted">Last Verified</span>
            <p className="text-text-primary">{agent.last_verified ? formatRelativeTime(agent.last_verified) : '—'}</p>
          </div>
          {agent.agent_card_url && (
            <div className="col-span-2">
              <span className="text-text-muted">Card URL</span>
              <p className="text-text-primary font-mono text-[11px] break-all">{agent.agent_card_url}</p>
            </div>
          )}
          {agent.network_allowlist_entries && agent.network_allowlist_entries.length > 0 && (
            <div className="col-span-2">
              <span className="text-text-muted">Network Allowlist</span>
              <div className="flex flex-wrap gap-1 mt-1">
                {agent.network_allowlist_entries.map((entry, i) => (
                  <span key={i} className="px-1.5 py-0.5 bg-surface-input rounded text-[10px] font-mono text-text-secondary">{entry}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Recent Receipts */}
      {agent.recent_receipts && agent.recent_receipts.length > 0 && (
        <div className="p-4 border-b border-border-default">
          <h4 className="text-xs font-semibold text-text-muted uppercase mb-2">Recent Receipts</h4>
          <div className="space-y-1 max-h-40 overflow-y-auto">
            {agent.recent_receipts.map((r, i) => (
              <div key={i} className="flex items-center justify-between text-[11px] py-1 border-b border-border-default/30 last:border-0">
                <span className="font-mono text-text-secondary">{String(r.action_type || '')}</span>
                <span className={`${String(r.status) === 'success' ? 'text-emerald-400' : 'text-red-400'}`}>
                  {String(r.status || '')}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="p-4 flex items-center gap-2 flex-wrap">
        <button onClick={handleVerify} disabled={verifying}
          className="px-3 py-1.5 text-xs font-medium rounded bg-surface-card-elevated text-text-primary border border-border-default hover:bg-surface-input disabled:opacity-50 transition-colors">
          {verifying ? 'Verifying...' : 'Re-verify Card'}
        </button>
        {(agent.direction === 'outbound' || agent.direction === 'both') && agent.status === 'active' && (
          <button onClick={onDelegate}
            className="px-3 py-1.5 text-xs font-medium rounded bg-amber-600/15 text-amber-400 border border-amber-600/30 hover:bg-amber-600/25 transition-colors">
            Delegate Task
          </button>
        )}
        {agent.status === 'active' && (
          <button onClick={handleRevoke} disabled={revoking}
            className="px-3 py-1.5 text-xs font-medium rounded bg-red-600/10 text-red-400 border border-red-600/30 hover:bg-red-600/20 disabled:opacity-50 transition-colors ml-auto">
            {revoking ? 'Revoking...' : 'Revoke Agent'}
          </button>
        )}
      </div>
    </div>
  )
}

// ── Agent Card Tab ────────────────────────────────────────────

function AgentCardTab() {
  const { data: card, refetch } = usePolling<LancelotAgentCard>({ fetcher: fetchOwnAgentCard, interval: 60_000 })
  const [regenerating, setRegenerating] = useState(false)

  const handleRegenerate = async () => {
    setRegenerating(true)
    try { await regenerateAgentCard(); refetch() } catch { /* */ }
    finally { setRegenerating(false) }
  }

  if (!card) {
    return (
      <div className="bg-surface-card border border-border-default rounded-lg p-8 text-center text-text-muted">
        Loading agent card...
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Card Identity */}
      <div className="bg-surface-card border border-border-default rounded-lg p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-lg font-semibold text-text-primary">{card.name}</h3>
            <p className="text-sm text-text-muted mt-1">{card.description}</p>
          </div>
          <button onClick={handleRegenerate} disabled={regenerating}
            className="px-4 py-2 text-sm font-medium rounded bg-accent-primary text-white hover:bg-accent-primary/90 disabled:opacity-50 transition-colors">
            {regenerating ? 'Regenerating...' : 'Regenerate Card'}
          </button>
        </div>

        <div className="grid grid-cols-4 gap-4">
          <div>
            <span className="text-xs text-text-muted block">Version</span>
            <span className="text-sm font-mono text-text-primary">{card.version}</span>
          </div>
          <div>
            <span className="text-xs text-text-muted block">Protocol</span>
            <span className="text-sm font-mono text-text-primary">A2A v{card.a2a_protocol_version}</span>
          </div>
          <div>
            <span className="text-xs text-text-muted block">Authentication</span>
            <span className="text-sm text-text-primary">{String(card.authentication?.type || 'none')}</span>
          </div>
          <div>
            <span className="text-xs text-text-muted block">URL</span>
            <span className="text-sm font-mono text-text-primary break-all">{card.url || '—'}</span>
          </div>
        </div>
      </div>

      {/* Advertised Skills */}
      <div className="bg-surface-card border border-border-default rounded-lg p-5">
        <h4 className="text-sm font-semibold text-text-primary mb-3">
          Advertised Skills
          <span className="ml-2 text-xs text-text-muted font-normal">({card.skills.length})</span>
        </h4>
        {card.skills.length === 0 ? (
          <p className="text-sm text-text-muted">No skills advertised. Enable skills in the Soul to expose them via A2A.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {card.skills.map((skill) => (
              <div key={skill.id} className="bg-surface-input rounded-lg p-3 border border-border-default/50">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-mono text-accent-primary">{skill.id}</span>
                  <span className="text-xs text-text-primary font-medium">{skill.name}</span>
                </div>
                <p className="text-[11px] text-text-muted">{skill.description}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Capabilities */}
      <div className="bg-surface-card border border-border-default rounded-lg p-5">
        <h4 className="text-sm font-semibold text-text-primary mb-3">Capabilities</h4>
        <div className="flex flex-wrap gap-2">
          {Object.entries(card.capabilities || {}).map(([key, enabled]) => (
            <span key={key} className={`px-2 py-1 rounded text-xs font-mono ${enabled ? 'bg-emerald-500/15 text-emerald-400' : 'bg-zinc-500/15 text-zinc-500 line-through'}`}>
              {key}
            </span>
          ))}
        </div>
      </div>

      {/* Governance Declaration */}
      {card.governance_declaration && (
        <div className="bg-surface-card border border-border-default rounded-lg p-5">
          <h4 className="text-sm font-semibold text-text-primary mb-3">Governance Declaration</h4>
          <pre className="text-xs font-mono text-text-secondary bg-surface-input rounded p-3 overflow-x-auto max-h-48">
            {JSON.stringify(card.governance_declaration, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

// ── Activity Tab ──────────────────────────────────────────────

function ActivityTab() {
  const { data } = usePolling<{ receipts: Record<string, unknown>[]; total: number }>({
    fetcher: () => fetchA2AReceipts(50),
    interval: 10_000,
  })

  const receipts = data?.receipts ?? []

  if (receipts.length === 0) {
    return <EmptyState title="No A2A Activity" description="A2A receipts will appear here once agents begin interacting." />
  }

  return (
    <div className="bg-surface-card border border-border-default rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border-default bg-surface-card-elevated">
            <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Time</th>
            <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Action</th>
            <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Agent</th>
            <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Status</th>
            <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Tier</th>
          </tr>
        </thead>
        <tbody>
          {receipts.map((r, i) => {
            const actionType = String(r.action_type || '')
            const status = String(r.status || '')
            const tier = r.tier != null ? Number(r.tier) : null
            const agentId = String(r.caller_agent_id || r.target_agent_id || (r.inputs as Record<string, unknown>)?.target_agent_id || '—')
            const ts = String(r.timestamp || r.created_at || '')
            return (
              <tr key={i} className="border-b border-border-default/30 hover:bg-surface-card-elevated/50 transition-colors">
                <td className="px-4 py-2 text-text-muted text-xs font-mono">{ts ? formatRelativeTime(ts) : '—'}</td>
                <td className="px-4 py-2 text-text-secondary text-xs font-mono">{actionType}</td>
                <td className="px-4 py-2 text-text-primary text-xs font-mono">{agentId}</td>
                <td className="px-4 py-2">
                  <span className={`text-xs font-medium ${status === 'success' ? 'text-emerald-400' : status === 'failed' ? 'text-red-400' : 'text-amber-400'}`}>
                    {status}
                  </span>
                </td>
                <td className="px-4 py-2">
                  {tier != null && <TrustBadge tier={tier} label="" />}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────

export function A2AManagement() {
  usePageTitle('A2A Protocol')

  const { data: status } = usePolling<A2AStatus>({ fetcher: fetchA2AStatus, interval: 15_000 })
  const { data: agentList, refetch: refetchAgents } = usePolling<AgentListResponse>({ fetcher: () => fetchRemoteAgents(), interval: 10_000 })

  const [activeTab, setActiveTab] = useState<TabId>('registry')
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [detailedAgent, setDetailedAgent] = useState<RemoteAgent | null>(null)
  const [showRegister, setShowRegister] = useState(false)
  const [delegateAgent, setDelegateAgent] = useState<RemoteAgent | null>(null)
  const [filterDirection, setFilterDirection] = useState<string>('')
  const [filterStatus, setFilterStatus] = useState<string>('')
  const [filterFramework, setFilterFramework] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  const agents = agentList?.agents ?? []

  // Apply client-side filters
  const filteredAgents = agents.filter((a) => {
    if (filterDirection && a.direction !== filterDirection) return false
    if (filterStatus && a.status !== filterStatus) return false
    if (filterFramework && a.agent_framework !== filterFramework) return false
    return true
  })

  // Fetch full agent detail when selected
  const selectAgent = useCallback(async (agentId: string) => {
    setSelectedAgentId(agentId)
    try {
      const detail = await fetchRemoteAgent(agentId)
      setDetailedAgent(detail)
    } catch {
      // Fall back to list data
      const fallback = agents.find((a) => a.agent_id === agentId) || null
      setDetailedAgent(fallback)
    }
  }, [agents])

  const handleRefresh = useCallback(() => {
    refetchAgents()
    if (selectedAgentId) selectAgent(selectedAgentId)
  }, [refetchAgents, selectedAgentId, selectAgent])

  // Disabled state
  if (status && !status.enabled) {
    return (
      <div className="p-6">
        <h2 className="text-xl font-semibold text-text-primary mb-2">A2A Protocol</h2>
        <div className="bg-surface-card border border-border-default rounded-lg p-6 text-center text-text-muted">
          A2A Protocol is disabled. Enable <code className="text-xs bg-surface-input px-1.5 py-0.5 rounded">FEATURE_A2A</code> and configure
          <code className="text-xs bg-surface-input px-1.5 py-0.5 rounded ml-1">a2a_permissions</code> in the Soul to activate.
        </div>
      </div>
    )
  }

  const tabs: { id: TabId; label: string; count?: number }[] = [
    { id: 'registry', label: 'Agent Registry', count: agents.length },
    { id: 'card', label: 'Lancelot Agent Card' },
    { id: 'activity', label: 'Activity Log' },
  ]

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-semibold text-text-primary">A2A Protocol</h2>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-400 border border-indigo-500/30">
            A2A v0.2
          </span>
          {status?.inbound_enabled && (
            <span className="text-[10px] px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">INBOUND</span>
          )}
          {status?.outbound_enabled && (
            <span className="text-[10px] px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20">OUTBOUND</span>
          )}
        </div>
        {activeTab === 'registry' && (
          <button onClick={() => setShowRegister(true)}
            className="px-4 py-2 text-sm font-medium rounded bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors">
            + Register Agent
          </button>
        )}
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm px-4 py-2 rounded flex items-center justify-between">
          {error}
          <button onClick={() => setError(null)} className="ml-2 underline text-xs">dismiss</button>
        </div>
      )}

      {/* Metrics Bar */}
      <div className="grid grid-cols-5 gap-3">
        <MetricCard label="Registered Agents" value={status?.registered_agents ?? 0} />
        <MetricCard label="Active" value={agents.filter((a) => a.status === 'active').length} />
        <MetricCard label="Inbound" value={status?.inbound_enabled ? 'Enabled' : 'Disabled'} />
        <MetricCard label="Outbound" value={status?.outbound_enabled ? 'Enabled' : 'Disabled'} />
        <MetricCard label="Max Delegation Depth" value={status?.max_delegation_depth ?? 0} />
      </div>

      {/* Tab Bar */}
      <div className="flex gap-1 border-b border-border-default">
        {tabs.map((tab) => (
          <button key={tab.id} onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.id
                ? 'border-accent-primary text-accent-primary'
                : 'border-transparent text-text-muted hover:text-text-primary'
            }`}>
            {tab.label}
            {tab.count != null && <span className="ml-1.5 text-xs text-text-muted">({tab.count})</span>}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'registry' && (
        <div className="grid grid-cols-5 gap-4">
          {/* Agent List — 3 cols */}
          <div className="col-span-3 space-y-3">
            {/* Filters */}
            <div className="flex items-center gap-2">
              <select value={filterDirection} onChange={(e) => setFilterDirection(e.target.value)}
                className="bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs text-text-primary focus:outline-none">
                <option value="">All Directions</option>
                <option value="inbound">Inbound</option>
                <option value="outbound">Outbound</option>
                <option value="both">Both</option>
              </select>
              <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}
                className="bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs text-text-primary focus:outline-none">
                <option value="">All Statuses</option>
                <option value="active">Active</option>
                <option value="suspended">Suspended</option>
                <option value="revoked">Revoked</option>
              </select>
              <select value={filterFramework} onChange={(e) => setFilterFramework(e.target.value)}
                className="bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs text-text-primary focus:outline-none">
                <option value="">All Frameworks</option>
                <option value="crewai">CrewAI</option>
                <option value="langchain">LangChain</option>
                <option value="google_adk">Google ADK</option>
                <option value="lancelot">Lancelot</option>
                <option value="unknown">Unknown</option>
              </select>
              <span className="text-xs text-text-muted ml-auto">{filteredAgents.length} agent{filteredAgents.length !== 1 ? 's' : ''}</span>
            </div>

            {/* Agent Table */}
            <div className="bg-surface-card border border-border-default rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border-default bg-surface-card-elevated">
                    <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Agent</th>
                    <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Direction</th>
                    <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Trust</th>
                    <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Card</th>
                    <th className="text-left px-4 py-2 text-text-muted font-medium text-xs">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredAgents.map((agent) => (
                    <tr key={agent.agent_id}
                      onClick={() => selectAgent(agent.agent_id)}
                      className={`border-b border-border-default/30 cursor-pointer transition-colors ${
                        selectedAgentId === agent.agent_id
                          ? 'bg-accent-primary/5'
                          : 'hover:bg-surface-card-elevated/50'
                      }`}>
                      <td className="px-4 py-2.5">
                        <div className="flex flex-col">
                          <span className="text-text-primary font-medium text-sm">{agent.display_name}</span>
                          <span className="text-[10px] font-mono text-text-muted">{agent.agent_id}</span>
                        </div>
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge className={DIRECTION_STYLES[agent.direction] || DIRECTION_STYLES.outbound}>
                          {agent.direction.toUpperCase()}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-1.5">
                          <TrustBadge tier={agent.inbound_trust_tier} label="IN" />
                          <TrustBadge tier={agent.outbound_trust_tier} label="OUT" />
                        </div>
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge className={CARD_STYLES[agent.card_status] || CARD_STYLES.unverified}>
                          {agent.card_status.toUpperCase()}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge className={STATUS_STYLES[agent.status] || STATUS_STYLES.active}>
                          {agent.status.toUpperCase()}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                  {filteredAgents.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-text-muted">
                        {agents.length === 0
                          ? 'No A2A agents registered. Click "Register Agent" to add one.'
                          : 'No agents match the current filters.'}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Detail Panel — 2 cols */}
          <div className="col-span-2">
            {detailedAgent ? (
              <AgentDetail
                agent={detailedAgent}
                onRefresh={handleRefresh}
                onDelegate={() => setDelegateAgent(detailedAgent)}
              />
            ) : (
              <div className="bg-surface-card border border-border-default rounded-lg p-8 text-center text-text-muted">
                <p className="text-sm">Select an agent to view details</p>
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'card' && <AgentCardTab />}
      {activeTab === 'activity' && <ActivityTab />}

      {/* Register Dialog */}
      {showRegister && (
        <RegisterDialog
          onClose={() => setShowRegister(false)}
          onRegistered={() => { refetchAgents(); setError(null) }}
        />
      )}

      {/* Delegate Dialog */}
      {delegateAgent && (
        <DelegateDialog
          agent={delegateAgent}
          onClose={() => setDelegateAgent(null)}
        />
      )}
    </div>
  )
}

export default A2AManagement
