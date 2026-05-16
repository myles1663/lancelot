import { useEffect, useState, type ReactNode } from 'react'
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
import type { InstalledSkillDetail, SkillProposalDetail } from '@/types/api'

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

function proposalPipelineState(proposal: { pipeline_passed: boolean; pipeline_failed_at_stage: string | null }): 'healthy' | 'degraded' | 'error' {
  if (proposal.pipeline_passed) return 'healthy'
  if (proposal.pipeline_failed_at_stage) return 'error'
  return 'degraded'
}

function proposalPipelineLabel(proposal: { pipeline_passed: boolean; pipeline_failed_at_stage: string | null }) {
  if (proposal.pipeline_passed) return 'ready for approval'
  if (proposal.pipeline_failed_at_stage) return `blocked at ${proposal.pipeline_failed_at_stage}`
  return 'checks pending'
}

function manifestSourceTone(source: string): 'healthy' | 'degraded' | 'inactive' {
  if (source === 'registry' || source === 'builtin') return 'healthy'
  if (source === 'missing') return 'degraded'
  return 'inactive'
}

function manifestSourceMessage(source: string) {
  if (source === 'registry') return 'Manifest loaded from the installed registry package.'
  if (source === 'builtin') return 'Manifest loaded from the built-in skill contract.'
  if (source === 'missing') return 'No manifest is available for this registry entry.'
  return 'Manifest source is not classified.'
}

function renderJson(value: unknown, emptyLabel: string) {
  if (value == null) {
    return emptyLabel
  }
  return JSON.stringify(value, null, 2)
}

function StatTile({ label, value, tone = 'neutral' }: { label: string; value: ReactNode; tone?: 'neutral' | 'healthy' | 'warning' | 'error' }) {
  const toneClass =
    tone === 'healthy'
      ? 'text-state-healthy'
      : tone === 'warning'
        ? 'text-state-degraded'
        : tone === 'error'
          ? 'text-state-error'
          : 'text-text-primary'
  return (
    <div className="border-t border-border-default bg-surface-card-elevated/50 px-4 py-3 lg:border-l lg:border-t-0 first:lg:border-l-0">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">{label}</p>
      <p className={`mt-1 font-mono text-lg font-semibold ${toneClass}`}>{value}</p>
    </div>
  )
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-1 border-b border-border-default/40 py-2 last:border-b-0 sm:grid-cols-[9rem_1fr]">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">{label}</span>
      <div className="min-w-0 text-xs text-text-primary">{children}</div>
    </div>
  )
}

function skillOwnershipClass(ownership: string) {
  if (ownership === 'system') return 'bg-accent-primary/10 text-accent-primary'
  if (ownership === 'marketplace') return 'bg-state-degraded/10 text-state-degraded'
  return 'bg-state-healthy/10 text-state-healthy'
}

function shouldUseCompactSkillsView() {
  if (typeof window === 'undefined') return false
  const params = new URLSearchParams(window.location.search)
  return params.get('compact') === '1'
}

export function SkillsPanel() {
  usePageTitle('Skills')
  const { data: proposals, refetch: refreshProposals } = usePolling({ fetcher: fetchSkillProposals, interval: 10000 })
  const { data: skills, refetch: refreshSkills } = usePolling({ fetcher: fetchInstalledSkills, interval: 15000 })

  const [selectedProposal, setSelectedProposal] = useState<SkillProposalDetail | null>(null)
  const [selectedSkill, setSelectedSkill] = useState<InstalledSkillDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [compactRender, setCompactRender] = useState(shouldUseCompactSkillsView)

  const pendingCount = proposals?.proposals.filter((p) => p.status === 'pending').length ?? 0
  const blockedCount = proposals?.proposals.filter((p) => p.status === 'review_failed').length ?? 0
  const approvedCount = proposals?.proposals.filter((p) => p.status === 'approved').length ?? 0
  const installedCount = skills?.skills.filter((s) => s.ownership !== 'system').length ?? 0
  const enabledCount = skills?.skills.filter((s) => s.enabled).length ?? 0

  useEffect(() => {
    const evaluate = () => {
      setCompactRender(shouldUseCompactSkillsView())
    }
    evaluate()
    window.addEventListener('resize', evaluate)
    return () => window.removeEventListener('resize', evaluate)
  }, [])

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
      const res = shouldEnable ? await enableInstalledSkill(name) : await disableInstalledSkill(name)
      setActionMessage(`${res.skill.name} ${res.status}.`)
      refreshSkills()
      if (selectedSkill?.name === name) {
        setSelectedSkill(await fetchInstalledSkill(name))
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Action failed'
      setActionMessage(`Error: ${message}`)
    } finally {
      setLoading(false)
    }
  }

  if (compactRender) {
    const proposalItems = proposals?.proposals ?? []
    const skillItems = skills?.skills ?? []

    return (
      <div className="space-y-4">
        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent-primary">Operations</p>
          <h2 className="mt-1 text-lg font-semibold text-text-primary">Governed Skill Pipeline</h2>
          <p className="mt-2 text-sm leading-relaxed text-text-secondary">
            Review proposed skills and inspect installed runtime capabilities.
          </p>
          <div className="mt-4 grid grid-cols-2 gap-2">
            <StatTile label="Pending" value={pendingCount} tone={pendingCount > 0 ? 'warning' : 'neutral'} />
            <StatTile label="Failed" value={blockedCount} tone={blockedCount > 0 ? 'error' : 'neutral'} />
            <StatTile label="Approved" value={approvedCount} tone={approvedCount > 0 ? 'healthy' : 'neutral'} />
            <StatTile label="Enabled" value={enabledCount} />
          </div>
        </section>

        {actionMessage && (
          <div className="rounded-md border border-border-default bg-surface-card-elevated p-3 text-sm text-text-primary">
            {actionMessage}
          </div>
        )}

        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-text-primary">Proposal Queue</h3>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-[10px] text-text-muted">
              {proposalItems.length}
            </span>
          </div>
          {proposalItems.length === 0 ? (
            <p className="text-sm text-text-muted">No governed skill proposals are waiting for review.</p>
          ) : (
            <div className="space-y-2">
              {proposalItems.map((proposal) => (
                <button
                  key={proposal.id}
                  type="button"
                  onClick={() => viewProposal(proposal.id)}
                  className="w-full rounded-md border border-border-default bg-surface-card-elevated p-3 text-left"
                >
                  <p className="break-all text-sm font-mono text-text-primary">{proposal.name}</p>
                  <p className="mt-1 text-xs text-text-secondary">{proposal.status} / {proposal.risk}</p>
                  <p className="mt-1 text-xs leading-relaxed text-text-muted">{proposal.description || 'No description'}</p>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-text-primary">Installed Skills</h3>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-[10px] text-text-muted">
              {skillItems.length}
            </span>
          </div>
          {skillItems.length === 0 ? (
            <p className="text-sm text-text-muted">Loading installed skills...</p>
          ) : (
            <div className="space-y-2">
              {skillItems.map((skill) => (
                <div key={skill.name} className="rounded-md border border-border-default bg-surface-card-elevated p-3">
                  <p className="break-all text-sm font-mono text-text-primary">{skill.name}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-text-secondary">
                    <span>v{skill.version}</span>
                    <span>{skill.ownership}</span>
                    <span>{skill.enabled ? 'enabled' : 'disabled'}</span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => viewInstalledSkill(skill.name)}
                      className="rounded-md border border-border-default px-3 py-1.5 text-xs font-medium text-text-secondary"
                    >
                      Inspect
                    </button>
                    <button
                      type="button"
                      disabled={loading}
                      onClick={() => handleInstalledSkillToggle(skill.name, !skill.enabled)}
                      className={`rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-50 ${
                        skill.enabled ? 'bg-state-error/10 text-state-error' : 'bg-state-healthy/10 text-state-healthy'
                      }`}
                    >
                      {skill.enabled ? 'Disable' : 'Enable'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {selectedProposal && (
          <section className="rounded-lg border border-border-default bg-surface-card p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="break-all text-sm font-semibold text-text-primary">{selectedProposal.name}</h3>
                <p className="mt-1 text-xs text-text-muted">{selectedProposal.description || 'No description'}</p>
              </div>
              <button type="button" onClick={() => setSelectedProposal(null)} className="text-xs text-text-muted">
                Close
              </button>
            </div>
            <div className="mt-4 space-y-2 text-xs text-text-secondary">
              <p>Status: {selectedProposal.status}</p>
              <p>Pipeline: {selectedProposal.pipeline_passed ? 'passed' : selectedProposal.pipeline_failed_at_stage ?? 'blocked'}</p>
              <p>Permissions: {selectedProposal.permissions.join(', ') || 'none'}</p>
              <p>Capabilities: {selectedProposal.approved_capabilities.join(', ') || 'none'}</p>
            </div>
            <pre className="mt-4 max-h-72 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs text-text-primary">
              {selectedProposal.manifest_yaml || '(no runtime manifest)'}
            </pre>
          </section>
        )}

        {selectedSkill && (
          <section className="rounded-lg border border-border-default bg-surface-card p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="break-all text-sm font-semibold text-text-primary">{selectedSkill.name}</h3>
                <p className="mt-1 text-xs text-text-muted">{selectedSkill.description || 'Installed skill registry record.'}</p>
              </div>
              <button type="button" onClick={() => setSelectedSkill(null)} className="text-xs text-text-muted">
                Close
              </button>
            </div>
            <div className="mt-4 space-y-2 text-xs text-text-secondary">
              <p>Version: {selectedSkill.version}</p>
              <p>Status: {selectedSkill.enabled ? 'enabled' : 'disabled'}</p>
              <p>Ownership: {selectedSkill.ownership}</p>
              <p>Signature: {selectedSkill.signature_state}</p>
              <p>Risk: {selectedSkill.risk || 'not declared'}</p>
              <p>Manifest source: {selectedSkill.manifest_source}</p>
              <p className="break-all">Manifest path: {selectedSkill.manifest_path || 'not recorded'}</p>
            </div>
            <div className="mt-4">
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-text-muted">Permissions</p>
              {renderPills(selectedSkill.permissions ?? [], 'No permissions declared')}
            </div>
            <pre className="mt-4 max-h-48 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs text-text-primary">
              {renderJson({ inputs: selectedSkill.inputs, outputs: selectedSkill.outputs }, '(no contract declared)')}
            </pre>
            <pre className="mt-4 max-h-72 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs text-text-primary">
              {renderJson(selectedSkill.manifest, '(no manifest stored for this registry entry)')}
            </pre>
          </section>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <section className="overflow-hidden rounded-lg border border-border-default bg-surface-card">
        <div className="flex flex-col gap-4 p-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent-primary">Operations</p>
            <h2 className="mt-1 text-xl font-semibold text-text-primary">Governed Skill Pipeline</h2>
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">
              Review proposed skills, inspect installed runtime capabilities, and enable or disable skills without leaving the War Room.
            </p>
          </div>
          <div className="rounded-md border border-accent-primary/25 bg-accent-primary/10 px-3 py-2">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-accent-primary">Installed Enabled</p>
            <p className="mt-1 font-mono text-lg font-semibold text-text-primary">{enabledCount}</p>
          </div>
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-4">
          <StatTile label="Pending Review" value={pendingCount} tone={pendingCount > 0 ? 'warning' : 'neutral'} />
          <StatTile label="Review Failed" value={blockedCount} tone={blockedCount > 0 ? 'error' : 'neutral'} />
          <StatTile label="Approved" value={approvedCount} tone={approvedCount > 0 ? 'healthy' : 'neutral'} />
          <StatTile label="Non-System Installed" value={installedCount} />
        </div>
      </section>

      {actionMessage && (
        <div className="rounded-md border border-border-default bg-surface-card-elevated p-3 text-sm text-text-primary">
          {actionMessage}
        </div>
      )}

      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Pipeline Intake</p>
            <h3 className="text-sm font-semibold text-text-primary">Proposal Queue</h3>
          </div>
          <span className="w-fit rounded-md border border-border-default bg-surface-input px-2 py-1 text-[10px] font-mono text-text-muted">
            {proposals?.total ?? 0} proposals
          </span>
        </div>
        {!proposals || proposals.proposals.length === 0 ? (
          <p className="rounded-md border border-border-default bg-surface-card-elevated p-3 text-sm text-text-muted">
            No governed skill proposals are waiting for review.
          </p>
        ) : (
          <div className="space-y-2">
            {proposals.proposals.map((proposal) => (
              <button
                key={proposal.id}
                type="button"
                onClick={() => viewProposal(proposal.id)}
                className="w-full rounded-md border border-border-default bg-surface-card-elevated p-3 text-left transition-all hover:border-accent-primary/50 hover:bg-surface-input/40"
              >
                <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="break-all text-sm font-mono text-text-primary">{proposal.name}</span>
                      <StatusDot state={STATUS_STATES[proposal.status] ?? 'inactive'} label={proposal.status} />
                      <StatusDot
                        state={proposalPipelineState(proposal)}
                        label={proposalPipelineLabel(proposal)}
                      />
                    </div>
                    <p className="mt-1 text-xs leading-relaxed text-text-muted">{proposal.description || 'No description'}</p>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-[10px] text-text-muted sm:grid-cols-4">
                      <span className="rounded border border-border-default bg-surface-input px-2 py-1">Risk: {proposal.risk}</span>
                      <span className="rounded border border-border-default bg-surface-input px-2 py-1">Source: {proposal.source}</span>
                      <span className="rounded border border-border-default bg-surface-input px-2 py-1">
                        Permissions: {proposal.permissions.length}
                      </span>
                      <span className="rounded border border-border-default bg-surface-input px-2 py-1">
                        Vault keys: {proposal.credential_keys.length}
                      </span>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 text-[10px] text-text-muted">
                    <span>{formatDateOnly(proposal.created_at)}</span>
                    <span className="rounded border border-border-default px-2 py-1 text-text-secondary">Inspect</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Runtime Registry</p>
            <h3 className="text-sm font-semibold text-text-primary">Installed Skills</h3>
          </div>
          <span className="w-fit rounded-md border border-border-default bg-surface-input px-2 py-1 text-[10px] font-mono text-text-muted">
            {skills?.total ?? 0} installed
          </span>
        </div>
        {!skills || skills.skills.length === 0 ? (
          <p className="rounded-md border border-border-default bg-surface-card-elevated p-3 text-sm text-text-muted">
            Loading installed skills...
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
            {skills.skills.map((skill) => (
              <div key={skill.name} className="rounded-md border border-border-default bg-surface-card-elevated p-3">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="break-all text-sm font-mono text-text-primary">{skill.name}</span>
                      <span className="text-[10px] font-mono text-text-muted">v{skill.version}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-mono ${skillOwnershipClass(skill.ownership)}`}>
                        {skill.ownership}
                      </span>
                      <StatusDot state={skill.enabled ? 'healthy' : 'inactive'} label={skill.enabled ? 'enabled' : 'disabled'} />
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <button
                      type="button"
                      onClick={() => viewInstalledSkill(skill.name)}
                      className="rounded-md border border-border-default px-3 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:border-accent-primary/60 hover:text-text-primary"
                    >
                      Inspect
                    </button>
                    <button
                      type="button"
                      disabled={loading}
                      onClick={() => handleInstalledSkillToggle(skill.name, !skill.enabled)}
                      className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50 ${
                        skill.enabled
                          ? 'bg-state-error/10 text-state-error hover:bg-state-error/20'
                          : 'bg-state-healthy/10 text-state-healthy hover:bg-state-healthy/20'
                      }`}
                    >
                      {skill.enabled ? 'Disable' : 'Enable'}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {selectedProposal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm">
          <div className="flex max-h-[88vh] w-full max-w-6xl flex-col rounded-lg border border-border-default bg-surface-card shadow-xl">
            <div className="flex items-start justify-between gap-4 border-b border-border-default p-4">
              <div>
                <h3 className="text-base font-semibold text-text-primary">{selectedProposal.name}</h3>
                <p className="mt-0.5 text-xs text-text-muted">{selectedProposal.description || 'No description'}</p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedProposal(null)}
                className="p-1.5 text-text-muted transition-colors hover:text-text-primary"
                aria-label="Close proposal detail"
              >
                X
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
                    className="rounded-md bg-state-healthy/10 px-3 py-1.5 text-xs font-medium text-state-healthy transition-colors hover:bg-state-healthy/20 disabled:opacity-50"
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm">
          <div className="flex max-h-[88vh] w-full max-w-5xl flex-col rounded-lg border border-border-default bg-surface-card shadow-xl">
            <div className="flex items-start justify-between gap-4 border-b border-border-default p-4">
              <div>
                <h3 className="text-base font-semibold text-text-primary">{selectedSkill.name}</h3>
                <p className="mt-0.5 text-xs text-text-muted">Installed skill registry record and manifest detail.</p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedSkill(null)}
                className="p-1.5 text-text-muted transition-colors hover:text-text-primary"
                aria-label="Close installed skill detail"
              >
                X
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
                <MetricCard label="Version" value={selectedSkill.version} />
                <MetricCard label="Status" value={selectedSkill.enabled ? 'enabled' : 'disabled'} />
                <MetricCard label="Ownership" value={selectedSkill.ownership} />
                <MetricCard label="Signature" value={selectedSkill.signature_state} />
              </div>

              <section className="rounded-lg border border-border-default bg-surface-card-elevated p-4">
                <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <h4 className="text-xs font-medium uppercase tracking-wider text-text-secondary">
                    Registry Detail
                  </h4>
                  <StatusDot state={manifestSourceTone(selectedSkill.manifest_source)} label={selectedSkill.manifest_source} />
                </div>
                <div className="mb-3 rounded-md border border-border-default bg-surface-input px-3 py-2 text-xs text-text-secondary">
                  {manifestSourceMessage(selectedSkill.manifest_source)}
                </div>
                <DetailRow label="Description">
                  <span className="text-text-secondary">{selectedSkill.description || 'No description declared'}</span>
                </DetailRow>
                <DetailRow label="Risk">
                  <span className="font-mono text-[11px] text-text-secondary">{selectedSkill.risk || 'not declared'}</span>
                </DetailRow>
                <DetailRow label="Permissions">
                  {renderPills(selectedSkill.permissions ?? [], 'No permissions declared')}
                </DetailRow>
                <DetailRow label="Manifest Source">
                  <span className="font-mono text-[11px] text-text-secondary">{selectedSkill.manifest_source}</span>
                </DetailRow>
                <DetailRow label="Installed At">{formatTimestamp(selectedSkill.installed_at)}</DetailRow>
                <DetailRow label="Manifest Path">
                  <span className="break-all font-mono text-[11px] text-text-secondary">
                    {selectedSkill.manifest_path || 'Not recorded for this skill'}
                  </span>
                </DetailRow>
                <DetailRow label="Enabled">
                  <StatusDot state={selectedSkill.enabled ? 'healthy' : 'inactive'} label={selectedSkill.enabled ? 'enabled' : 'disabled'} />
                </DetailRow>
              </section>

              <section className="mt-6 rounded-lg border border-border-default bg-surface-card-elevated p-4">
                <h4 className="mb-3 text-xs font-medium uppercase tracking-wider text-text-secondary">
                  Capability Contract
                </h4>
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <div>
                    <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-text-muted">Inputs</p>
                    <pre className="max-h-64 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs font-mono text-text-primary">
                      {renderJson(selectedSkill.inputs, '(no inputs declared)')}
                    </pre>
                  </div>
                  <div>
                    <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-text-muted">Outputs</p>
                    <pre className="max-h-64 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs font-mono text-text-primary">
                      {renderJson(selectedSkill.outputs, '(no outputs declared)')}
                    </pre>
                  </div>
                </div>
              </section>

              <section className="mt-6">
                <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-secondary">
                  Manifest
                </h4>
                <pre className="max-h-[28rem] overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs font-mono text-text-primary">
                  {renderJson(selectedSkill.manifest, '(no manifest stored for this registry entry)')}
                </pre>
              </section>
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-border-default p-4">
              <button
                type="button"
                disabled={loading}
                onClick={() => handleInstalledSkillToggle(selectedSkill.name, !selectedSkill.enabled)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50 ${
                  selectedSkill.enabled
                    ? 'bg-state-error/10 text-state-error hover:bg-state-error/20'
                    : 'bg-state-healthy/10 text-state-healthy hover:bg-state-healthy/20'
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
