import { useState } from 'react'
import { usePolling } from '@/hooks'
import {
  fetchSkillProposals,
  fetchSkillProposal,
  approveSkillProposal,
  rejectSkillProposal,
  installSkillProposal,
  fetchInstalledSkills,
} from '@/api'
import { StatusDot, MetricCard } from '@/components'
import type { SkillProposalDetail } from '@/types/api'

const STATUS_STATES: Record<string, 'healthy' | 'degraded' | 'error' | 'inactive'> = {
  pending: 'degraded',
  approved: 'healthy',
  rejected: 'error',
  installed: 'healthy',
}

export function SkillsPanel() {
  const { data: proposals, refetch: refreshProposals } = usePolling({ fetcher: fetchSkillProposals, interval: 10000 })
  const { data: skills, refetch: refreshSkills } = usePolling({ fetcher: fetchInstalledSkills, interval: 15000 })

  const [selectedProposal, setSelectedProposal] = useState<SkillProposalDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  const pendingCount = proposals?.proposals.filter((p) => p.status === 'pending').length ?? 0
  const approvedCount = proposals?.proposals.filter((p) => p.status === 'approved').length ?? 0
  const installedCount = skills?.skills.filter((s) => s.ownership === 'dynamic').length ?? 0
  const builtinCount = skills?.skills.filter((s) => s.ownership === 'system').length ?? 0

  const viewProposal = async (id: string) => {
    try {
      const detail = await fetchSkillProposal(id)
      setSelectedProposal(detail)
    } catch {
      setActionMessage('Failed to load proposal detail.')
    }
  }

  const handleAction = async (action: 'approve' | 'reject' | 'install', id: string) => {
    setLoading(true)
    setActionMessage(null)
    try {
      if (action === 'approve') {
        const res = await approveSkillProposal(id)
        setActionMessage(`Approved: ${res.name}`)
      } else if (action === 'reject') {
        const res = await rejectSkillProposal(id)
        setActionMessage(`Rejected: ${res.name}`)
      } else if (action === 'install') {
        const res = await installSkillProposal(id)
        setActionMessage(res.message)
      }
      setSelectedProposal(null)
      refreshProposals()
      refreshSkills()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Action failed'
      setActionMessage(`Error: ${msg}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Skills Manager</h2>

      {/* Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard label="Builtin Skills" value={builtinCount} />
        <MetricCard label="Dynamic Skills" value={installedCount} />
        <MetricCard label="Pending Proposals" value={pendingCount} />
        <MetricCard label="Approved" value={approvedCount} />
      </div>

      {actionMessage && (
        <div className="mb-4 p-3 rounded-md bg-surface-card-elevated border border-border-default text-sm text-text-primary">
          {actionMessage}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Skill Proposals */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Skill Proposals
          </h3>
          {!proposals || proposals.proposals.length === 0 ? (
            <p className="text-sm text-text-muted">
              No proposals yet. Lancelot will propose new skills when needed.
            </p>
          ) : (
            <div className="space-y-2">
              {proposals.proposals.map((p) => (
                <div
                  key={p.id}
                  className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md cursor-pointer hover:ring-1 hover:ring-accent-primary/30 transition-all"
                  onClick={() => viewProposal(p.id)}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-mono text-text-primary truncate">{p.name}</span>
                      <StatusDot
                        state={STATUS_STATES[p.status] ?? 'inactive'}
                        label={p.status}
                      />
                    </div>
                    <p className="text-[11px] text-text-muted mt-0.5 truncate">{p.description}</p>
                  </div>
                  <span className="text-[10px] text-text-muted ml-2 whitespace-nowrap">
                    {new Date(p.created_at).toLocaleDateString()}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Installed Skills */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Installed Skills
          </h3>
          {!skills || skills.skills.length === 0 ? (
            <p className="text-sm text-text-muted">Loading...</p>
          ) : (
            <div className="space-y-2">
              {skills.skills.map((s) => (
                <div key={s.name} className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-mono text-text-primary">{s.name}</span>
                    <span className="text-[10px] text-text-muted font-mono">v{s.version}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                      s.ownership === 'system'
                        ? 'bg-accent-primary/10 text-accent-primary'
                        : 'bg-state-success/10 text-state-success'
                    }`}>
                      {s.ownership}
                    </span>
                    <StatusDot
                      state={s.enabled ? 'healthy' : 'inactive'}
                      label={s.enabled ? 'enabled' : 'disabled'}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {/* Proposal Detail Modal */}
      {selectedProposal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="bg-surface-card border border-border-default rounded-lg shadow-xl max-w-2xl w-full mx-4 max-h-[80vh] flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-border-default">
              <div>
                <h3 className="text-base font-semibold text-text-primary">{selectedProposal.name}</h3>
                <p className="text-xs text-text-muted mt-0.5">{selectedProposal.description}</p>
              </div>
              <button
                onClick={() => setSelectedProposal(null)}
                className="p-1.5 text-text-muted hover:text-text-primary transition-colors"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M4 4L12 12M12 4L4 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {/* Meta */}
              <div className="flex items-center gap-3 text-xs">
                <StatusDot
                  state={STATUS_STATES[selectedProposal.status] ?? 'inactive'}
                  label={selectedProposal.status}
                />
                <span className="text-text-muted">
                  Created: {new Date(selectedProposal.created_at).toLocaleString()}
                </span>
                {selectedProposal.permissions.length > 0 && (
                  <span className="text-text-muted">
                    Permissions: {selectedProposal.permissions.join(', ')}
                  </span>
                )}
              </div>

              {/* Code Preview */}
              <div>
                <h4 className="text-xs font-medium text-text-secondary uppercase tracking-wider mb-2">
                  Implementation
                </h4>
                <pre className="bg-surface-input border border-border-default rounded-md p-3 text-xs font-mono text-text-primary overflow-x-auto max-h-60 overflow-y-auto whitespace-pre">
                  {selectedProposal.execute_code || '(no code)'}
                </pre>
              </div>

              {/* Manifest */}
              {selectedProposal.manifest_yaml && (
                <div>
                  <h4 className="text-xs font-medium text-text-secondary uppercase tracking-wider mb-2">
                    Manifest
                  </h4>
                  <pre className="bg-surface-input border border-border-default rounded-md p-3 text-xs font-mono text-text-muted overflow-x-auto max-h-40 overflow-y-auto whitespace-pre">
                    {selectedProposal.manifest_yaml}
                  </pre>
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="flex items-center justify-end gap-2 p-4 border-t border-border-default">
              {selectedProposal.status === 'pending' && (
                <>
                  <button
                    onClick={() => handleAction('reject', selectedProposal.id)}
                    disabled={loading}
                    className="px-3 py-1.5 text-xs font-medium rounded-md bg-state-error/10 text-state-error hover:bg-state-error/20 transition-colors disabled:opacity-50"
                  >
                    Reject
                  </button>
                  <button
                    onClick={() => handleAction('approve', selectedProposal.id)}
                    disabled={loading}
                    className="px-3 py-1.5 text-xs font-medium rounded-md bg-state-success/10 text-state-success hover:bg-state-success/20 transition-colors disabled:opacity-50"
                  >
                    Approve
                  </button>
                </>
              )}
              {selectedProposal.status === 'approved' && (
                <button
                  onClick={() => handleAction('install', selectedProposal.id)}
                  disabled={loading}
                  className="px-3 py-1.5 text-xs font-medium rounded-md bg-accent-primary/10 text-accent-primary hover:bg-accent-primary/20 transition-colors disabled:opacity-50"
                >
                  Install Skill
                </button>
              )}
              <button
                onClick={() => setSelectedProposal(null)}
                className="px-3 py-1.5 text-xs font-medium rounded-md bg-surface-input text-text-secondary hover:text-text-primary transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
