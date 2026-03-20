import { useState, useCallback } from 'react'
import { usePolling } from '@/hooks'
import {
  fetchA2AStatus,
  fetchRemoteAgents,
  fetchOwnAgentCard,
  registerRemoteAgent,
  revokeRemoteAgent,
  verifyAgentCard,
  regenerateAgentCard,
  type A2AStatus,
  type AgentListResponse,
  type LancelotAgentCard,
} from '@/api/a2a'

// ── Badges ──────────────────────────────────────────────────

function DirectionBadge({ direction }: { direction: string }) {
  const colors: Record<string, string> = {
    inbound: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
    outbound: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    both: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  }
  return (
    <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono border uppercase ${colors[direction] || colors.outbound}`}>
      {direction}
    </span>
  )
}

function FrameworkBadge({ framework }: { framework: string }) {
  const colors: Record<string, string> = {
    crewai: 'bg-emerald-500/20 text-emerald-400',
    langchain: 'bg-orange-500/20 text-orange-400',
    google_adk: 'bg-blue-500/20 text-blue-400',
    lancelot: 'bg-indigo-500/20 text-indigo-400',
    unknown: 'bg-zinc-500/20 text-zinc-400',
  }
  return (
    <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono ${colors[framework] || colors.unknown}`}>
      {framework.toUpperCase()}
    </span>
  )
}

function CardStatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    verified: 'bg-emerald-500/20 text-emerald-400',
    stale: 'bg-amber-500/20 text-amber-400',
    unverified: 'bg-red-500/20 text-red-400',
  }
  return (
    <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono ${styles[status] || styles.unverified}`}>
      {status.toUpperCase()}
    </span>
  )
}

function TrustTierBadge({ tier, label }: { tier: number; label: string }) {
  const colors = ['text-emerald-400', 'text-blue-400', 'text-amber-400', 'text-red-400']
  return (
    <span className="text-[10px] text-zinc-500">
      {label}: <span className={`font-mono ${colors[tier] || colors[2]}`}>T{tier}</span>
    </span>
  )
}

// ── Register Form ───────────────────────────────────────────

function RegisterForm({ onRegister }: { onRegister: () => void }) {
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
        agent_card_url: cardUrl.trim(),
        agent_framework: framework,
        direction,
      })
      setAgentId('')
      setDisplayName('')
      setCardUrl('')
      onRegister()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Registration failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="p-3 bg-zinc-800/50 rounded-lg border border-zinc-700 space-y-2">
      <h4 className="text-[11px] font-semibold text-zinc-400 uppercase">Register Remote Agent</h4>
      <div className="grid grid-cols-2 gap-2">
        <input
          type="text" value={agentId} onChange={(e) => setAgentId(e.target.value)}
          placeholder="Agent ID" className="bg-zinc-900 border border-zinc-600 rounded px-2 py-1.5 text-xs font-mono text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
        />
        <input
          type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Display Name" className="bg-zinc-900 border border-zinc-600 rounded px-2 py-1.5 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
        />
        <input
          type="text" value={cardUrl} onChange={(e) => setCardUrl(e.target.value)}
          placeholder="Agent Card URL" className="col-span-2 bg-zinc-900 border border-zinc-600 rounded px-2 py-1.5 text-xs font-mono text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
        />
        <select value={framework} onChange={(e) => setFramework(e.target.value)} className="bg-zinc-900 border border-zinc-600 rounded px-2 py-1.5 text-xs text-zinc-200 focus:outline-none">
          <option value="unknown">Unknown</option>
          <option value="crewai">CrewAI</option>
          <option value="langchain">LangChain</option>
          <option value="google_adk">Google ADK</option>
        </select>
        <select value={direction} onChange={(e) => setDirection(e.target.value)} className="bg-zinc-900 border border-zinc-600 rounded px-2 py-1.5 text-xs text-zinc-200 focus:outline-none">
          <option value="outbound">Outbound</option>
          <option value="inbound">Inbound</option>
          <option value="both">Both</option>
        </select>
      </div>
      {error && <p className="text-[11px] text-red-400">{error}</p>}
      <button
        onClick={handleSubmit} disabled={submitting || !agentId.trim() || !displayName.trim()}
        className="px-3 py-1.5 text-[11px] font-medium rounded bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-zinc-700 disabled:text-zinc-500 transition-colors"
      >
        {submitting ? 'Registering...' : 'Register Agent'}
      </button>
    </div>
  )
}

// ── Agent Card Viewer ───────────────────────────────────────

function OwnCardViewer() {
  const { data: card } = usePolling<LancelotAgentCard>({ fetcher: fetchOwnAgentCard, interval: 60_000 })
  const [regenerating, setRegenerating] = useState(false)

  const handleRegenerate = async () => {
    setRegenerating(true)
    try { await regenerateAgentCard() } catch { /* */ }
    finally { setRegenerating(false) }
  }

  if (!card) return null

  return (
    <div className="p-3 bg-zinc-800/50 rounded-lg border border-zinc-700 space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-[11px] font-semibold text-zinc-400 uppercase">Lancelot Agent Card</h4>
        <button onClick={handleRegenerate} disabled={regenerating}
          className="px-2 py-1 text-[10px] font-medium rounded bg-zinc-700 text-zinc-300 hover:bg-zinc-600 disabled:opacity-50 transition-colors">
          {regenerating ? 'Regenerating...' : 'Regenerate'}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div><span className="text-zinc-500">Name:</span> <span className="text-zinc-200">{card.name}</span></div>
        <div><span className="text-zinc-500">Version:</span> <span className="text-zinc-200 font-mono">{card.version}</span></div>
        <div><span className="text-zinc-500">Protocol:</span> <span className="text-zinc-200 font-mono">A2A v{card.a2a_protocol_version}</span></div>
        <div><span className="text-zinc-500">Auth:</span> <span className="text-zinc-200">{String(card.authentication?.type || 'none')}</span></div>
      </div>
      <div>
        <span className="text-[10px] text-zinc-500">Advertised Skills ({card.skills.length}):</span>
        <div className="flex flex-wrap gap-1 mt-1">
          {card.skills.map((s) => (
            <span key={s.id} className="px-1.5 py-0.5 bg-indigo-500/20 text-indigo-400 text-[9px] rounded font-mono">{s.id}</span>
          ))}
        </div>
      </div>
      {card.governance_declaration && (
        <div className="text-[10px] text-zinc-500">
          Governance: <span className="text-emerald-400">declared</span> (framework: {String(card.governance_declaration.governance_framework)})
        </div>
      )}
    </div>
  )
}

// ── Main Section ────────────────────────────────────────────

export function A2ASection() {
  const { data: status } = usePolling<A2AStatus>({ fetcher: fetchA2AStatus, interval: 15_000 })
  const { data: agentList, refetch } = usePolling<AgentListResponse>({ fetcher: () => fetchRemoteAgents(), interval: 10_000 })
  const [showRegister, setShowRegister] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [verifying, setVerifying] = useState<string | null>(null)

  const agents = agentList?.agents ?? []

  const handleVerify = useCallback(async (agentId: string) => {
    setVerifying(agentId)
    try { await verifyAgentCard(agentId); refetch() } catch { /* */ }
    finally { setVerifying(null) }
  }, [refetch])

  const handleRevoke = useCallback(async (agentId: string) => {
    try { await revokeRemoteAgent(agentId); refetch() } catch { /* */ }
  }, [refetch])

  if (!status?.enabled) return null

  return (
    <section className="mt-6">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-zinc-300">A2A Remote Agents</h3>
          <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">
            A2A v0.2
          </span>
          {status.inbound_enabled && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400">INBOUND</span>
          )}
          {status.outbound_enabled && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">OUTBOUND</span>
          )}
        </div>
        <button onClick={() => setShowRegister(!showRegister)}
          className="px-2 py-1 text-[10px] font-medium rounded bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors">
          {showRegister ? 'Cancel' : '+ Register Agent'}
        </button>
      </div>

      {showRegister && <RegisterForm onRegister={() => { setShowRegister(false); refetch() }} />}

      {/* Agent List */}
      {agents.length === 0 ? (
        <div className="p-4 bg-zinc-800/30 border border-zinc-700 rounded-lg text-center text-zinc-500 text-sm">
          No A2A agents registered. Register an agent or enable inbound A2A for auto-registration.
        </div>
      ) : (
        <div className="space-y-1">
          {agents.map((agent) => (
            <div key={agent.agent_id} className="bg-zinc-800/50 border border-zinc-700 rounded-lg overflow-hidden">
              {/* Agent Row */}
              <div
                className="flex items-center justify-between p-3 cursor-pointer hover:bg-zinc-800/80 transition-colors"
                onClick={() => setExpanded(expanded === agent.agent_id ? null : agent.agent_id)}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className={`text-[10px] transition-transform ${expanded === agent.agent_id ? 'rotate-90' : ''}`}>&#9654;</span>
                  <span className="text-sm font-medium text-zinc-200 truncate">{agent.display_name}</span>
                  <span className="text-[10px] font-mono text-zinc-500">{agent.agent_id.slice(0, 12)}</span>
                  <DirectionBadge direction={agent.direction} />
                  <FrameworkBadge framework={agent.agent_framework} />
                  <CardStatusBadge status={agent.card_status} />
                </div>
                <div className="flex items-center gap-3 flex-shrink-0">
                  <TrustTierBadge tier={agent.inbound_trust_tier} label="IN" />
                  <TrustTierBadge tier={agent.outbound_trust_tier} label="OUT" />
                  <span className={`text-[9px] px-1.5 py-0.5 rounded ${agent.status === 'active' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                    {agent.status.toUpperCase()}
                  </span>
                </div>
              </div>

              {/* Expanded Detail */}
              {expanded === agent.agent_id && (
                <div className="px-3 pb-3 border-t border-zinc-700/50 space-y-2">
                  <div className="grid grid-cols-3 gap-3 mt-2 text-[11px]">
                    <div>
                      <span className="text-zinc-500 block">Agent Card URL</span>
                      <span className="text-zinc-300 font-mono text-[10px] break-all">{agent.agent_card_url || '—'}</span>
                    </div>
                    <div>
                      <span className="text-zinc-500 block">Auth Type</span>
                      <span className="text-zinc-300">{agent.auth_type}</span>
                    </div>
                    <div>
                      <span className="text-zinc-500 block">Interactions</span>
                      <span className="text-zinc-300 font-mono">
                        {agent.interaction_count} total, {agent.success_count} success
                      </span>
                    </div>
                    <div>
                      <span className="text-zinc-500 block">Last Interaction</span>
                      <span className="text-zinc-300 font-mono text-[10px]">
                        {agent.last_interaction ? new Date(agent.last_interaction).toLocaleString() : '—'}
                      </span>
                    </div>
                    <div>
                      <span className="text-zinc-500 block">Last Outcome</span>
                      <span className={`font-mono text-[10px] ${agent.last_outcome === 'completed' ? 'text-emerald-400' : agent.last_outcome === 'failed' ? 'text-red-400' : 'text-zinc-400'}`}>
                        {agent.last_outcome || '—'}
                      </span>
                    </div>
                    <div>
                      <span className="text-zinc-500 block">Kill Switch</span>
                      <span className="text-zinc-300 font-mono text-[10px]">{agent.kill_switch_id}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 mt-2">
                    <button onClick={() => handleVerify(agent.agent_id)} disabled={verifying === agent.agent_id}
                      className="px-2 py-1 text-[10px] font-medium rounded bg-zinc-700 text-zinc-300 hover:bg-zinc-600 disabled:opacity-50 transition-colors">
                      {verifying === agent.agent_id ? 'Verifying...' : 'Re-verify Card'}
                    </button>
                    {agent.status === 'active' && (
                      <button onClick={() => handleRevoke(agent.agent_id)}
                        className="px-2 py-1 text-[10px] font-medium rounded bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors">
                        Revoke
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Lancelot's Own Agent Card */}
      <div className="mt-3">
        <OwnCardViewer />
      </div>
    </section>
  )
}
