import { useState, useEffect, useCallback } from 'react'
import { usePolling } from '@/hooks'
import { fetchSoulStatus } from '@/api'
import { fetchSoulContent, proposeSoulAmendment, approveSoulProposal, activateSoulProposal } from '@/api/soul'
import { ConfirmDialog } from '@/components'
import type { SoulDocument, SoulContentResponse, SoulProposal } from '@/types/api'

// ── Collapsible Section ─────────────────────────────────────────────
function Section({ title, children, defaultOpen = true }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-border-default rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between p-3 bg-surface-card-elevated hover:bg-surface-input/50 transition-colors"
      >
        <h4 className="text-xs font-medium text-text-secondary uppercase tracking-wider">{title}</h4>
        <span className={`text-[10px] text-text-muted transition-transform ${open ? 'rotate-90' : ''}`}>&#9654;</span>
      </button>
      {open && <div className="p-3 bg-surface-card border-t border-border-default/50">{children}</div>}
    </div>
  )
}

// ── Tag List ────────────────────────────────────────────────────────
const TAG_STYLES: Record<string, string> = {
  'state-healthy': 'bg-state-healthy/10 text-state-healthy border-state-healthy/20',
  'state-degraded': 'bg-state-degraded/10 text-state-degraded border-state-degraded/20',
  'state-error': 'bg-state-error/10 text-state-error border-state-error/20',
  'accent-primary': 'bg-accent-primary/10 text-accent-primary border-accent-primary/20',
}

function TagList({ items, color = 'accent-primary' }: { items: string[]; color?: string }) {
  const style = TAG_STYLES[color] ?? TAG_STYLES['accent-primary']
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item, i) => (
        <span key={i} className={`text-[11px] font-mono px-2 py-0.5 rounded border ${style}`}>
          {item}
        </span>
      ))}
    </div>
  )
}

// ── Key/Value Row ───────────────────────────────────────────────────
function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 py-1.5">
      <span className="text-[10px] text-text-muted uppercase tracking-wider w-28 flex-shrink-0 pt-0.5">{label}</span>
      <div className="flex-1 text-xs text-text-primary">{children}</div>
    </div>
  )
}

// ── Soul Viewer ─────────────────────────────────────────────────────
function SoulViewer({ soul }: { soul: SoulDocument }) {
  return (
    <div className="space-y-3">
      {/* Mission & Allegiance */}
      <Section title="Mission & Identity">
        <div className="space-y-3">
          <div>
            <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Mission</p>
            <p className="text-sm text-text-primary leading-relaxed">{soul.mission}</p>
          </div>
          <div>
            <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1">Allegiance</p>
            <p className="text-sm text-text-primary leading-relaxed">{soul.allegiance}</p>
          </div>
        </div>
      </Section>

      {/* Autonomy Posture */}
      <Section title="Autonomy Posture">
        <div className="space-y-3">
          <KV label="Level">
            <span className={`inline-block px-2 py-0.5 rounded text-[11px] font-semibold ${
              soul.autonomy_posture.level === 'supervised'
                ? 'bg-state-degraded/15 text-state-degraded'
                : soul.autonomy_posture.level === 'autonomous'
                  ? 'bg-state-error/15 text-state-error'
                  : 'bg-state-healthy/15 text-state-healthy'
            }`}>
              {soul.autonomy_posture.level.toUpperCase()}
            </span>
          </KV>
          <KV label="Description">
            <p className="text-xs text-text-secondary leading-relaxed">{soul.autonomy_posture.description}</p>
          </KV>
          <div>
            <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1.5">Allowed Autonomous</p>
            <TagList items={soul.autonomy_posture.allowed_autonomous} color="state-healthy" />
          </div>
          <div>
            <p className="text-[10px] text-text-muted uppercase tracking-wider mb-1.5">Requires Approval</p>
            <TagList items={soul.autonomy_posture.requires_approval} color="state-degraded" />
          </div>
        </div>
      </Section>

      {/* Risk Rules */}
      <Section title={`Risk Rules (${soul.risk_rules.length})`}>
        <div className="space-y-2">
          {soul.risk_rules.map((rule, i) => (
            <div key={i} className="p-2 bg-surface-card-elevated rounded border border-border-default/50">
              <div className="flex items-center gap-2 mb-1">
                <span className={`w-1.5 h-1.5 rounded-full ${rule.enforced ? 'bg-state-healthy' : 'bg-state-error'}`} />
                <span className="text-xs font-mono text-text-primary">{rule.name}</span>
                <span className={`text-[9px] px-1.5 py-0.5 rounded ${
                  rule.enforced ? 'bg-state-healthy/15 text-state-healthy' : 'bg-state-error/15 text-state-error'
                }`}>
                  {rule.enforced ? 'ENFORCED' : 'DISABLED'}
                </span>
              </div>
              <p className="text-[11px] text-text-secondary leading-relaxed pl-3.5">{rule.description}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* Approval Rules */}
      <Section title="Approval Rules">
        <div className="space-y-1">
          <KV label="Timeout">{soul.approval_rules.default_timeout_seconds}s ({Math.round(soul.approval_rules.default_timeout_seconds / 60)} min)</KV>
          <KV label="On Timeout">
            <span className="font-mono text-[11px]">{soul.approval_rules.escalation_on_timeout}</span>
          </KV>
          <KV label="Channels">
            <TagList items={soul.approval_rules.channels} />
          </KV>
        </div>
      </Section>

      {/* Tone Invariants */}
      <Section title={`Tone Invariants (${soul.tone_invariants.length})`}>
        <ul className="space-y-1.5">
          {soul.tone_invariants.map((inv, i) => (
            <li key={i} className="flex items-start gap-2 text-xs text-text-primary">
              <span className="text-accent-primary mt-0.5">&#8226;</span>
              <span>{inv}</span>
            </li>
          ))}
        </ul>
      </Section>

      {/* Memory Ethics */}
      <Section title={`Memory Ethics (${soul.memory_ethics.length})`}>
        <ul className="space-y-1.5">
          {soul.memory_ethics.map((rule, i) => (
            <li key={i} className="flex items-start gap-2 text-xs text-text-primary">
              <span className="text-accent-primary mt-0.5">&#8226;</span>
              <span>{rule}</span>
            </li>
          ))}
        </ul>
      </Section>

      {/* Scheduling Boundaries */}
      <Section title="Scheduling Boundaries">
        <div className="space-y-1">
          <KV label="Max Concurrent">{soul.scheduling_boundaries.max_concurrent_jobs} jobs</KV>
          <KV label="Max Duration">{soul.scheduling_boundaries.max_job_duration_seconds}s ({Math.round(soul.scheduling_boundaries.max_job_duration_seconds / 60)} min)</KV>
          <KV label="No Auto-Irreversible">
            <span className={`text-[11px] font-semibold ${soul.scheduling_boundaries.no_autonomous_irreversible ? 'text-state-healthy' : 'text-state-error'}`}>
              {soul.scheduling_boundaries.no_autonomous_irreversible ? 'ENFORCED' : 'DISABLED'}
            </span>
          </KV>
          <KV label="Require Ready">
            <span className={`text-[11px] font-semibold ${soul.scheduling_boundaries.require_ready_state ? 'text-state-healthy' : 'text-state-error'}`}>
              {soul.scheduling_boundaries.require_ready_state ? 'YES' : 'NO'}
            </span>
          </KV>
          {soul.scheduling_boundaries.description && (
            <KV label="Description">
              <p className="text-xs text-text-secondary leading-relaxed">{soul.scheduling_boundaries.description}</p>
            </KV>
          )}
        </div>
      </Section>
    </div>
  )
}

// ── YAML Editor ─────────────────────────────────────────────────────
function SoulEditor({ rawYaml, onProposed }: { rawYaml: string; onProposed: () => void }) {
  const [draft, setDraft] = useState(rawYaml)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const hasChanges = draft !== rawYaml

  const handlePropose = async () => {
    setSaving(true)
    setResult(null)
    try {
      const res = await proposeSoulAmendment(draft)
      setResult({
        type: 'success',
        message: `Proposal ${res.proposal_id} created (${res.diff_summary.length} change${res.diff_summary.length !== 1 ? 's' : ''}). Go to Pending Proposals to approve and activate.`,
      })
      onProposed()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to create proposal'
      setResult({ type: 'error', message: msg })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[10px] text-text-muted uppercase tracking-wider">Edit Soul YAML</p>
        {hasChanges && <span className="text-[10px] text-state-degraded">Unsaved changes</span>}
      </div>
      <textarea
        value={draft}
        onChange={e => setDraft(e.target.value)}
        rows={24}
        className="w-full bg-surface-input border border-border-default rounded px-3 py-2 text-xs font-mono text-text-primary leading-relaxed placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary resize-y"
        spellCheck={false}
      />
      <p className="text-[10px] text-text-muted">
        Edits create an amendment proposal that must be approved and activated. The soul linter will validate changes before proposals are created. Critical linter failures will be rejected.
      </p>
      <div className="flex items-center gap-3">
        <button
          onClick={handlePropose}
          disabled={saving || !hasChanges}
          className={`px-4 py-1.5 text-[11px] font-medium rounded transition-colors ${
            hasChanges
              ? 'bg-accent-primary text-white hover:bg-accent-primary/80'
              : 'bg-surface-input text-text-muted cursor-not-allowed'
          }`}
        >
          {saving ? 'Proposing...' : 'Propose Amendment'}
        </button>
        {hasChanges && (
          <button
            onClick={() => { setDraft(rawYaml); setResult(null) }}
            className="px-3 py-1.5 text-[11px] text-text-muted hover:text-text-primary transition-colors"
          >
            Reset
          </button>
        )}
      </div>
      {result && (
        <div className={`p-2 rounded border text-[11px] leading-relaxed ${
          result.type === 'success'
            ? 'bg-state-healthy/10 border-state-healthy/30 text-state-healthy'
            : 'bg-state-error/10 border-state-error/30 text-state-error'
        }`}>
          {result.message}
        </div>
      )}
    </div>
  )
}

// ── Main Soul Inspector Page ────────────────────────────────────────
export function SoulInspector() {
  const { data: statusData, refetch: refetchStatus } = usePolling({ fetcher: fetchSoulStatus, interval: 30000 })
  const [content, setContent] = useState<SoulContentResponse | null>(null)
  const [contentLoading, setContentLoading] = useState(true)
  const [tab, setTab] = useState<'view' | 'edit'>('view')
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [confirmAction, setConfirmAction] = useState<{ type: 'approve' | 'activate'; id: string } | null>(null)
  const [actionResult, setActionResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const loadContent = useCallback(async () => {
    try {
      setContentLoading(true)
      const res = await fetchSoulContent()
      setContent(res)
    } catch {
      // soul not loaded
    } finally {
      setContentLoading(false)
    }
  }, [])

  useEffect(() => { loadContent() }, [loadContent])

  const handleProposalAction = async () => {
    if (!confirmAction) return
    setActionLoading(confirmAction.id)
    setActionResult(null)
    try {
      if (confirmAction.type === 'approve') {
        await approveSoulProposal(confirmAction.id)
        setActionResult({ type: 'success', message: `Proposal ${confirmAction.id} approved. You can now activate it.` })
      } else {
        const res = await activateSoulProposal(confirmAction.id)
        setActionResult({ type: 'success', message: `Soul activated: ${res.active_version ?? 'new version'}` })
        loadContent() // refresh the viewer
      }
      refetchStatus()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Action failed'
      setActionResult({ type: 'error', message: msg })
    } finally {
      setActionLoading(null)
      setConfirmAction(null)
    }
  }

  const proposals = statusData?.pending_proposals ?? []

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-text-primary">Soul Inspector</h2>
        {statusData && (
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-state-healthy" />
            <span className="text-sm font-mono font-bold text-text-primary">{statusData.active_version}</span>
          </div>
        )}
      </div>

      {contentLoading && !content ? (
        <p className="text-text-muted text-sm">Loading soul data...</p>
      ) : !content ? (
        <div className="bg-surface-card border border-border-default rounded-lg p-6 text-center">
          <p className="text-text-muted">Soul system not initialized. Enable FEATURE_SOUL in Kill Switches.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {/* View / Edit tabs */}
          <div className="flex gap-1 bg-surface-card border border-border-default rounded-lg p-1">
            <button
              onClick={() => setTab('view')}
              className={`flex-1 px-4 py-1.5 rounded text-xs font-medium transition-colors ${
                tab === 'view'
                  ? 'bg-accent-primary text-white'
                  : 'text-text-muted hover:text-text-primary'
              }`}
            >
              Constitution Viewer
            </button>
            <button
              onClick={() => setTab('edit')}
              className={`flex-1 px-4 py-1.5 rounded text-xs font-medium transition-colors ${
                tab === 'edit'
                  ? 'bg-accent-primary text-white'
                  : 'text-text-muted hover:text-text-primary'
              }`}
            >
              YAML Editor
            </button>
          </div>

          {/* Tab content */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            {tab === 'view' ? (
              <SoulViewer soul={content.soul} />
            ) : (
              <SoulEditor
                rawYaml={content.raw_yaml}
                onProposed={() => { refetchStatus(); loadContent() }}
              />
            )}
          </section>

          {/* Pending Proposals */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Pending Proposals ({proposals.length})
            </h3>

            {actionResult && (
              <div className={`mb-3 p-2 rounded border text-[11px] leading-relaxed ${
                actionResult.type === 'success'
                  ? 'bg-state-healthy/10 border-state-healthy/30 text-state-healthy'
                  : 'bg-state-error/10 border-state-error/30 text-state-error'
              }`}>
                {actionResult.message}
                <button onClick={() => setActionResult(null)} className="ml-2 text-text-muted hover:text-text-primary">&times;</button>
              </div>
            )}

            {proposals.length === 0 ? (
              <p className="text-sm text-text-muted">No pending amendments</p>
            ) : (
              <div className="space-y-3">
                {proposals.map((p: SoulProposal) => {
                  const pid = p.proposal_id || p.id || ''
                  return (
                    <div key={pid} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-mono text-text-muted">{pid}</span>
                          <span className={`text-[9px] px-1.5 py-0.5 rounded font-semibold ${
                            p.status === 'pending' ? 'bg-state-degraded/15 text-state-degraded' :
                            p.status === 'approved' ? 'bg-state-healthy/15 text-state-healthy' :
                            'bg-surface-input text-text-muted'
                          }`}>
                            {p.status.toUpperCase()}
                          </span>
                          {p.proposed_version && (
                            <span className="text-[10px] text-text-muted">
                              &rarr; {p.proposed_version}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          {p.status === 'pending' && (
                            <button
                              onClick={() => setConfirmAction({ type: 'approve', id: pid })}
                              disabled={actionLoading === pid}
                              className="px-2 py-1 text-[10px] font-medium rounded bg-state-healthy/15 text-state-healthy hover:bg-state-healthy/25 transition-colors"
                            >
                              Approve
                            </button>
                          )}
                          {p.status === 'approved' && (
                            <button
                              onClick={() => setConfirmAction({ type: 'activate', id: pid })}
                              disabled={actionLoading === pid}
                              className="px-2 py-1 text-[10px] font-medium rounded bg-accent-primary/15 text-accent-primary hover:bg-accent-primary/25 transition-colors"
                            >
                              Activate
                            </button>
                          )}
                        </div>
                      </div>
                      {p.diff_summary && p.diff_summary.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {p.diff_summary.map((d, i) => (
                            <span key={i} className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                              d.startsWith('added') ? 'bg-state-healthy/10 text-state-healthy' :
                              d.startsWith('removed') ? 'bg-state-error/10 text-state-error' :
                              'bg-state-degraded/10 text-state-degraded'
                            }`}>
                              {d}
                            </span>
                          ))}
                        </div>
                      )}
                      {p.author && (
                        <p className="text-[10px] text-text-muted mt-1">by {p.author} {p.created_at ? `at ${new Date(p.created_at).toLocaleString()}` : ''}</p>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          {/* Available Versions */}
          {statusData && statusData.available_versions.length > 0 && (
            <section className="bg-surface-card border border-border-default rounded-lg p-4">
              <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
                Available Versions ({statusData.available_versions.length})
              </h3>
              <div className="flex flex-wrap gap-2">
                {statusData.available_versions.map(v => (
                  <div
                    key={v}
                    className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-mono ${
                      v === statusData.active_version
                        ? 'bg-accent-primary/10 border border-accent-primary/30 text-accent-primary font-semibold'
                        : 'bg-surface-card-elevated border border-border-default text-text-secondary'
                    }`}
                  >
                    {v}
                    {v === statusData.active_version && (
                      <span className="text-[9px] uppercase tracking-wider">active</span>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirmAction !== null}
        title={confirmAction?.type === 'approve' ? 'Approve Proposal' : 'Activate Soul Version'}
        description={
          confirmAction?.type === 'approve'
            ? `Approve amendment proposal ${confirmAction?.id ?? ''}? This marks it ready for activation.`
            : `Activate proposal ${confirmAction?.id ?? ''}? This will change the active soul version. The soul linter will validate the change before activation.`
        }
        variant={confirmAction?.type === 'activate' ? 'destructive' : 'default'}
        confirmLabel={confirmAction?.type === 'approve' ? 'Approve' : 'Activate'}
        onConfirm={handleProposalAction}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  )
}
