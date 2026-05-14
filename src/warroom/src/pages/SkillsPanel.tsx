import { useState } from 'react'
import { usePolling, usePageTitle } from '@/hooks'
import {
  fetchSkillProposal,
  fetchSkillProposals,
  approveSkillProposal,
  rejectSkillProposal,
  installSkillProposal,
  fetchInstalledSkills,
  fetchInstalledSkill,
  enableInstalledSkill,
  disableInstalledSkill,
} from '@/api'
import { MetricCard, StatusDot } from '@/components'
import { formatDateOnly, formatTimestamp } from '@/utils/dateFormat'
import type { SkillProposalDetail, InstalledSkillDetail } from '@/types/api'

const STATUS_STATES: Record<string, 'healthy' | 'degraded' | 'error' | 'inactive'> = {
  pending: 'degraded',
  review_failed: 'error',
  approved: 'healthy',
  rejected: 'inactive',
  installed: 'healthy',
}

function renderPills(values: string[], emptyLabel: string) {
  if (values.length === 0) {
    return <span className="text-xs text-text-muted">{emptyLabel}</span>
  }
  return (
    <div className="flex flex-wrap gap-2">
      {values.map((value) => (
        <span
          key={value}
          className="rounded-md border border-border-default bg-surface-input px-2 py-1 text-[11px] font-mono text-text-secondary"
        >
          {value}
        </span>
      ))}
    </div>
  )
}

function describeStage(stageData: unknown): string {
  if (!stageData || typeof stageData !== 'object') {
    return 'No data'
  }
  const data = stageData as Record<string, unknown>
  if (typeof data.status === 'string') {
    return data.status
  }
  if (typeof data.passed === 'boolean') {
    return data.passed ? 'passed' : 'failed'
  }
  if (typeof data.error === 'string') {
    return data.error
  }
  return 'Recorded'
}

function renderJson(value: unknown, emptyLabel: string) {
  if (value === null || value === undefined || (Array.isArray(value) && value.length === 0)) {
    return <span className="text-xs text-text-muted">{emptyLabel}</span>
  }
  if (typeof value === 'object' && !Array.isArray(value) && Object.keys(value as Record<string, unknown>).length === 0) {
    return <span className="text-xs text-text-muted">{emptyLabel}</span>
  }
  return (
    <pre className="max-h-56 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-[11px] text-text-muted">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

export function SkillsPanel() {
  usePageTitle('Skills')
  const { data: proposals, refetch: refreshProposals } = usePolling({ fetcher: fetchSkillProposals, interval: 10000 })
  const { data: skills, refetch: refreshSkills } = usePolling({ fetcher: fetchInstalledSkills, interval: 15000 })

  const [selectedProposal, setSelectedProposal] = useState<SkillProposalDetail | null>(null)
  const [selectedSkill, setSelectedSkill] = useState<InstalledSkillDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  const pendingCount = proposals?.proposals.filter((p) => p.status === 'pending').length ?? 0
  const blockedCount = proposals?.proposals.filter((p) => p.status === 'review_failed').length ?? 0
  const approvedCount = proposals?.proposals.filter((p) => p.status === 'approved').length ?? 0
  const installedCount = skills?.skills.filter((s) => s.ownership !== 'system').length ?? 0

  const viewProposal = async (id: string) => {
    try {
      const detail = await fetchSkillProposal(id)
      setSelectedProposal(detail)
    } catch {
      setActionMessage('Failed to load skill proposal detail.')
    }
  }

  const viewInstalledSkill = async (name: string) => {
    try {
      const detail = await fetchInstalledSkill(name)
      setSelectedSkill(detail)
    } catch {
      setActionMessage('Failed to load installed skill detail.')
    }
  }

  const handleAction = async (action: 'approve' | 'reject' | 'install', id: string) => {
    setLoading(true)
    setActionMessage(null)
    try {
      if (action === 'approve') {
        const res = await approveSkillProposal(id)
        setActionMessage(`Approved ${res.name} for installation review.`)
      } else if (action === 'reject') {
        const res = await rejectSkillProposal(id)
        setActionMessage(`Rejected ${res.name}${res.rejected_reason ? `: ${res.rejected_reason}` : ''}`)
      } else {
        const res = await installSkillProposal(id)
        setActionMessage(
          `${res.message}${res.validated_capabilities.length ? ` Validated capabilities: ${res.validated_capabilities.join(', ')}` : ''}`,
        )
      }
      setSelectedProposal(null)
      refreshProposals()
      refreshSkills()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Action failed'
      setActionMessage(`Error: ${message}`)
    } finally {
      setLoading(false)
    }
  }

  const handleInstalledSkillToggle = async (name: string, shouldEnable: boolean) => {
    setLoading(true)
    setActionMessage(null)
    try {
      const detail = shouldEnable ? await enableInstalledSkill(name) : await disableInstalledSkill(name)
      setSelectedSkill(detail)
      setActionMessage(`${detail.name} ${detail.enabled ? 'enabled' : 'disabled'}.`)
      refreshSkills()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Action failed'
      setActionMessage(`Error: ${message}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h2 className="mb-6 text-lg font-semibold text-text-primary">Governed Skill Pipeline</h2>

      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <MetricCard label="Pending Review" value={pendingCount} />
        <MetricCard label="Review Failed" value={blockedCount} />
        <MetricCard label="Approved" value={approvedCount} />
        <MetricCard label="Installed Custom" value={installedCount} />
      </div>

      {actionMessage && (
        <div className="mb-4 rounded-md border border-border-default bg-surface-card-elevated p-3 text-sm text-text-primary">
          {actionMessage}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <h3 className="mb-3 text-sm font-medium uppercase tracking-wider text-text-secondary">
            Proposal Queue
          </h3>
          {!proposals || proposals.proposals.length === 0 ? (
            <p className="text-sm text-text-muted">No governed skill proposals are waiting for review.</p>
          ) : (
            <div className="space-y-2">
              {proposals.proposals.map((proposal) => (
                <button
                  key={proposal.id}
                  type="button"
                  onClick={() => viewProposal(proposal.id)}
                  className="w-full rounded-md bg-surface-card-elevated p-3 text-left transition-all hover:ring-1 hover:ring-accent-primary/30"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-mono text-text-primary">{proposal.name}</span>
                        <StatusDot state={STATUS_STATES[proposal.status] ?? 'inactive'} label={proposal.status} />
                        <StatusDot
                          state={proposal.pipeline_passed ? 'healthy' : 'error'}
                          label={proposal.pipeline_passed ? 'pipeline passed' : proposal.pipeline_failed_at_stage ?? 'pipeline blocked'}
                        />
                      </div>
                      <p className="mt-1 truncate text-[11px] text-text-muted">{proposal.description || 'No description'}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-text-muted">
                        <span>Risk: {proposal.risk}</span>
                        <span>Source: {proposal.source}</span>
                        {proposal.credential_keys.length > 0 && <span>Vault: {proposal.credential_keys.join(', ')}</span>}
                      </div>
                    </div>
                    <span className="whitespace-nowrap text-[10px] text-text-muted">{formatDateOnly(proposal.created_at)}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <h3 className="mb-3 text-sm font-medium uppercase tracking-wider text-text-secondary">
            Installed Skills
          </h3>
          {!skills ? (
            <p className="text-sm text-text-muted">Loading installed skills…</p>
          ) : skills.skills.length === 0 ? (
            <p className="text-sm text-text-muted">No installed skills are registered.</p>
          ) : (
            <div className="space-y-2">
              {skills.skills.map((skill) => (
                <div key={skill.name} className="rounded-md bg-surface-card-elevated p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-mono text-text-primary">{skill.name}</span>
                        <span className="text-[10px] font-mono text-text-muted">v{skill.version}</span>
                        <StatusDot state={skill.enabled ? 'healthy' : 'inactive'} label={skill.enabled ? 'enabled' : 'disabled'} />
                      </div>
                      <p className="mt-1 line-clamp-2 text-[11px] text-text-muted">{skill.description || 'No manifest description available'}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-text-muted">
                        <span>Risk: {skill.risk || 'unknown'}</span>
                        <span>Permissions: {skill.permissions.length || 0}</span>
                        <span
                          className={`rounded px-1.5 py-0.5 font-mono ${
                            skill.ownership === 'system'
                              ? 'bg-accent-primary/10 text-accent-primary'
                              : 'bg-state-success/10 text-state-success'
                          }`}
                        >
                          {skill.ownership}
                        </span>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => viewInstalledSkill(skill.name)}
                      className="rounded-md bg-surface-input px-3 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:text-text-primary"
                    >
                      Inspect
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {selectedProposal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="mx-4 flex max-h-[88vh] w-full max-w-5xl flex-col rounded-lg border border-border-default bg-surface-card shadow-xl">
            <div className="flex items-start justify-between border-b border-border-default p-4">
              <div>
                <h3 className="text-base font-semibold text-text-primary">{selectedProposal.name}</h3>
                <p className="mt-0.5 text-xs text-text-muted">{selectedProposal.description || 'No description'}</p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedProposal(null)}
                className="p-1.5 text-text-muted transition-colors hover:text-text-primary"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M4 4L12 12M12 4L4 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
                <MetricCard label="Status" value={selectedProposal.status} />
                <MetricCard label="Risk" value={selectedProposal.risk} />
                <MetricCard label="Source" value={selectedProposal.source} />
                <MetricCard
                  label="Pipeline"
                  value={selectedProposal.pipeline_passed ? 'passed' : selectedProposal.pipeline_failed_at_stage ?? 'blocked'}
                />
              </div>

              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <section className="rounded-lg border border-border-default bg-surface-card-elevated p-4">
                  <h4 className="mb-3 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Governance Contract
                  </h4>
                  <div className="space-y-4">
                    <div>
                      <p className="mb-2 text-[11px] uppercase tracking-wider text-text-muted">Runtime Permissions</p>
                      {renderPills(selectedProposal.permissions, 'No runtime permissions declared')}
                    </div>
                    <div>
                      <p className="mb-2 text-[11px] uppercase tracking-wider text-text-muted">Security Capabilities</p>
                      {renderPills(selectedProposal.approved_capabilities, 'No approved capabilities recorded')}
                    </div>
                    <div>
                      <p className="mb-2 text-[11px] uppercase tracking-wider text-text-muted">Target Domains</p>
                      {renderPills(selectedProposal.target_domains, 'No network domains declared')}
                    </div>
                    <div>
                      <p className="mb-2 text-[11px] uppercase tracking-wider text-text-muted">Vault Keys</p>
                      {renderPills(selectedProposal.credential_keys, 'No vault keys declared')}
                    </div>
                    <div className="grid grid-cols-1 gap-2 text-xs text-text-muted">
                      <span>Created: {formatTimestamp(selectedProposal.created_at)}</span>
                      {selectedProposal.approved_by && <span>Approved by: {selectedProposal.approved_by}</span>}
                      {selectedProposal.approved_at && <span>Approved at: {formatTimestamp(selectedProposal.approved_at)}</span>}
                      {selectedProposal.installed_at && <span>Installed at: {formatTimestamp(selectedProposal.installed_at)}</span>}
                      {selectedProposal.rejected_reason && <span>Rejected because: {selectedProposal.rejected_reason}</span>}
                    </div>
                  </div>
                </section>

                <section className="rounded-lg border border-border-default bg-surface-card-elevated p-4">
                  <h4 className="mb-3 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Security Pipeline
                  </h4>
                  <div className="space-y-2">
                    {Object.entries(selectedProposal.pipeline_stage_results).map(([stage, result]) => (
                      <div key={stage} className="rounded-md border border-border-default bg-surface-input p-3">
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-xs font-mono text-text-primary">{stage}</span>
                          <StatusDot
                            state={
                              describeStage(result) === 'passed' || describeStage(result) === 'approved'
                                ? 'healthy'
                                : describeStage(result) === 'pending'
                                  ? 'degraded'
                                  : 'error'
                            }
                            label={describeStage(result)}
                          />
                        </div>
                        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-[11px] text-text-muted">
                          {JSON.stringify(result, null, 2)}
                        </pre>
                      </div>
                    ))}
                  </div>
                </section>
              </div>

              <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
                <section>
                  <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Runtime Manifest
                  </h4>
                  <pre className="max-h-72 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs font-mono text-text-primary">
                    {selectedProposal.manifest_yaml || '(no runtime manifest)'}
                  </pre>
                </section>
                <section>
                  <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Security Manifest
                  </h4>
                  <pre className="max-h-72 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs font-mono text-text-primary">
                    {selectedProposal.security_manifest_yaml || '(no security manifest)'}
                  </pre>
                </section>
              </div>

              <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
                <section>
                  <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Implementation
                  </h4>
                  <pre className="max-h-80 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs font-mono text-text-primary">
                    {selectedProposal.execute_code || '(no implementation)'}
                  </pre>
                </section>
                <section>
                  <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Generated Tests
                  </h4>
                  <pre className="max-h-80 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs font-mono text-text-primary">
                    {selectedProposal.test_code || '(no generated tests)'}
                  </pre>
                </section>
              </div>

              <section className="mt-6 rounded-lg border border-border-default bg-surface-card-elevated p-4">
                <h4 className="mb-3 text-xs font-medium uppercase tracking-wider text-text-secondary">
                  Artifact Hashes
                </h4>
                <div className="space-y-2">
                  {Object.entries(selectedProposal.artifact_hashes).map(([artifact, digest]) => (
                    <div key={artifact} className="flex flex-col gap-1 rounded-md bg-surface-input p-3">
                      <span className="text-xs font-mono text-text-primary">{artifact}</span>
                      <span className="break-all text-[11px] text-text-muted">{digest}</span>
                    </div>
                  ))}
                </div>
              </section>
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-border-default p-4">
              {selectedProposal.status === 'pending' && selectedProposal.pipeline_passed && (
                <>
                  <button
                    type="button"
                    onClick={() => handleAction('reject', selectedProposal.id)}
                    disabled={loading}
                    className="rounded-md bg-state-error/10 px-3 py-1.5 text-xs font-medium text-state-error transition-colors hover:bg-state-error/20 disabled:opacity-50"
                  >
                    Reject
                  </button>
                  <button
                    type="button"
                    onClick={() => handleAction('approve', selectedProposal.id)}
                    disabled={loading}
                    className="rounded-md bg-state-success/10 px-3 py-1.5 text-xs font-medium text-state-success transition-colors hover:bg-state-success/20 disabled:opacity-50"
                  >
                    Approve
                  </button>
                </>
              )}
              {selectedProposal.status === 'approved' && (
                <button
                  type="button"
                  onClick={() => handleAction('install', selectedProposal.id)}
                  disabled={loading}
                  className="rounded-md bg-accent-primary/10 px-3 py-1.5 text-xs font-medium text-accent-primary transition-colors hover:bg-accent-primary/20 disabled:opacity-50"
                >
                  Install Skill
                </button>
              )}
              <button
                type="button"
                onClick={() => setSelectedProposal(null)}
                className="rounded-md bg-surface-input px-3 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:text-text-primary"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {selectedSkill && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="mx-4 flex max-h-[88vh] w-full max-w-5xl flex-col rounded-lg border border-border-default bg-surface-card shadow-xl">
            <div className="flex items-start justify-between border-b border-border-default p-4">
              <div>
                <h3 className="text-base font-semibold text-text-primary">{selectedSkill.name}</h3>
                <p className="mt-0.5 text-xs text-text-muted">{selectedSkill.description || 'No manifest description available'}</p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedSkill(null)}
                className="p-1.5 text-text-muted transition-colors hover:text-text-primary"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M4 4L12 12M12 4L4 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
                <MetricCard label="Status" value={selectedSkill.enabled ? 'enabled' : 'disabled'} />
                <MetricCard label="Risk" value={selectedSkill.risk || 'unknown'} />
                <MetricCard label="Ownership" value={selectedSkill.ownership || 'unknown'} />
                <MetricCard label="Signature" value={selectedSkill.signature_state || 'unknown'} />
              </div>

              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <section className="rounded-lg border border-border-default bg-surface-card-elevated p-4">
                  <h4 className="mb-3 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Runtime Contract
                  </h4>
                  <div className="space-y-4">
                    <div>
                      <p className="mb-2 text-[11px] uppercase tracking-wider text-text-muted">Permissions</p>
                      {renderPills(selectedSkill.permissions, 'No runtime permissions declared')}
                    </div>
                    <div>
                      <p className="mb-2 text-[11px] uppercase tracking-wider text-text-muted">Inputs</p>
                      {renderJson(selectedSkill.inputs, 'No inputs declared')}
                    </div>
                    <div>
                      <p className="mb-2 text-[11px] uppercase tracking-wider text-text-muted">Outputs</p>
                      {renderJson(selectedSkill.outputs, 'No outputs declared')}
                    </div>
                    <div className="grid grid-cols-1 gap-2 text-xs text-text-muted">
                      <span>Required brain: {selectedSkill.required_brain || 'not declared'}</span>
                      <span>Scheduler eligible: {selectedSkill.scheduler_eligible ? 'yes' : 'no'}</span>
                      {selectedSkill.installed_at && <span>Installed: {formatTimestamp(selectedSkill.installed_at)}</span>}
                      {selectedSkill.manifest_path && <span className="break-all">Manifest path: {selectedSkill.manifest_path}</span>}
                    </div>
                  </div>
                </section>

                <section className="rounded-lg border border-border-default bg-surface-card-elevated p-4">
                  <h4 className="mb-3 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Pipeline Position
                  </h4>
                  {selectedSkill.source_proposal ? (
                    <div className="space-y-4">
                      <div className="grid grid-cols-2 gap-3 text-xs text-text-muted">
                        <span>Proposal: {selectedSkill.source_proposal.id}</span>
                        <span>Status: {selectedSkill.source_proposal.status}</span>
                        <span>Source: {selectedSkill.source_proposal.source || 'unknown'}</span>
                        <span>Author: {selectedSkill.source_proposal.author || 'unknown'}</span>
                        {selectedSkill.source_proposal.approved_by && <span>Approved by: {selectedSkill.source_proposal.approved_by}</span>}
                        {selectedSkill.source_proposal.installed_at && <span>Installed: {formatTimestamp(selectedSkill.source_proposal.installed_at)}</span>}
                      </div>
                      <div>
                        <p className="mb-2 text-[11px] uppercase tracking-wider text-text-muted">Approved Capabilities</p>
                        {renderPills(selectedSkill.source_proposal.approved_capabilities, 'No approved capabilities recorded')}
                      </div>
                      <div>
                        <p className="mb-2 text-[11px] uppercase tracking-wider text-text-muted">Pipeline Stages</p>
                        {renderJson(selectedSkill.source_proposal.pipeline_stage_results, 'No pipeline stage history recorded')}
                      </div>
                    </div>
                  ) : (
                    <p className="text-sm text-text-muted">
                      No governed proposal is linked to this installed skill. System skills and older registry entries may not have proposal history.
                    </p>
                  )}
                </section>
              </div>

              <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
                <section>
                  <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Runtime Manifest
                  </h4>
                  <pre className="max-h-72 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs font-mono text-text-primary">
                    {selectedSkill.manifest_yaml || '(no runtime manifest available)'}
                  </pre>
                </section>
                <section>
                  <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Receipts Contract
                  </h4>
                  {renderJson(selectedSkill.receipts, 'No receipt config declared')}
                </section>
              </div>

              <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
                <section>
                  <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Implementation
                  </h4>
                  <pre className="max-h-80 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs font-mono text-text-primary">
                    {selectedSkill.execute_code || '(no implementation artifact available)'}
                  </pre>
                </section>
                <section>
                  <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Tests
                  </h4>
                  <pre className="max-h-80 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs font-mono text-text-primary">
                    {selectedSkill.test_code || '(no test artifact available)'}
                  </pre>
                </section>
              </div>

              {selectedSkill.source_proposal && (
                <section className="mt-6 rounded-lg border border-border-default bg-surface-card-elevated p-4">
                  <h4 className="mb-3 text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Proposal Artifact Hashes
                  </h4>
                  {renderJson(selectedSkill.source_proposal.artifact_hashes, 'No artifact hashes recorded')}
                </section>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-border-default p-4">
              <button
                type="button"
                onClick={() => handleInstalledSkillToggle(selectedSkill.name, !selectedSkill.enabled)}
                disabled={loading}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50 ${
                  selectedSkill.enabled
                    ? 'bg-state-error/10 text-state-error hover:bg-state-error/20'
                    : 'bg-state-success/10 text-state-success hover:bg-state-success/20'
                }`}
              >
                {selectedSkill.enabled ? 'Disable Skill' : 'Enable Skill'}
              </button>
              <button
                type="button"
                onClick={() => setSelectedSkill(null)}
                className="rounded-md bg-surface-input px-3 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:text-text-primary"
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
