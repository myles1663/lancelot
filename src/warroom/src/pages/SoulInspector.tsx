import { useState, useEffect, useCallback } from 'react'
import { usePolling, usePageTitle } from '@/hooks'
import { fetchSoulStatus, fetchCrusaderStatus } from '@/api'
import { fetchSoulContent, proposeSoulAmendment, approveSoulProposal, activateSoulProposal, activateSoulVersion, fetchSoulTemplates, fetchSoulTemplateDetail, applySoulTemplate, evaluateSoulCapability, fetchSoulBehaviorContract, saveSoulBehaviorContract, runSoulBehaviorContract } from '@/api/soul'
import { ConfirmDialog } from '@/components'
import { formatTimestamp } from '@/utils/dateFormat'
import type {
  SoulDocument,
  SoulContentResponse,
  SoulProposal,
  CrusaderStatusResponse,
  SoulOverlayInfo,
  SoulTemplateMetadata,
  SoulTemplateDetail,
  SoulRiskOverride,
  SoulTrustCeiling,
  SoulConnectorPolicy,
  SoulDataBoundary,
  SoulExternalTransmissionRule,
  SoulKillSwitchRule,
  SoulEvaluateResponse,
  SoulBehaviorContractCase,
  SoulBehaviorContractRunResponse,
  SoulVersionSource,
} from '@/types/api'

function soulSourceLabel(source?: SoulVersionSource) {
  if (!source) return 'source unknown'
  if (source.kind === 'template' && source.template_name) return `template: ${source.template_name}`
  if (source.kind === 'proposal' && source.author) return `proposal by ${source.author}`
  if (source.kind === 'baseline') return 'baseline'
  return source.kind
}

type SoulWorkflow = {
  id: 'view' | 'edit' | 'templates'
  label: string
  kicker: string
  description: string
}

const SOUL_WORKFLOWS: [SoulWorkflow, ...SoulWorkflow[]] = [
  {
    id: 'view',
    label: 'Constitution',
    kicker: 'Inspect and test',
    description: 'Read the active Soul, review governed controls, and run behavior checks.',
  },
  {
    id: 'edit',
    label: 'YAML Editor',
    kicker: 'Propose amendments',
    description: 'Create governed amendment proposals against the active constitution.',
  },
  {
    id: 'templates',
    label: 'Templates',
    kicker: 'Apply patterns',
    description: 'Browse or apply reusable Soul operating patterns as proposals.',
  },
]

const DEFAULT_SOUL_WORKFLOW = SOUL_WORKFLOWS[0]

type SoulConfirmAction =
  | { type: 'approve'; id: string }
  | { type: 'activate'; id: string }
  | { type: 'activate-version'; version: string }

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
const TIER_OPTIONS = ['T0', 'T1', 'T2', 'T3']
const GOVERNANCE_SECTION_KEYS = [
  'risk_overrides',
  'trust_ceilings',
  'connector_policies',
  'data_boundaries',
  'external_transmission_rules',
  'kill_switch_rules',
] as const
const GOVERNANCE_SECTION_SET = new Set<string>(GOVERNANCE_SECTION_KEYS)
const FIELD_CLASS = 'w-full bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary'
const LABEL_CLASS = 'text-[10px] text-text-muted uppercase tracking-wider'

type ConnectorPolicyDraft = Omit<SoulConnectorPolicy, 'verified_recipients' | 'allowed_channels' | 'max_sends_per_day'> & {
  connector: string
  verified_recipients_text: string
  allowed_channels_text: string
  max_sends_per_day_text: string
}

type DataBoundaryDraft = Omit<SoulDataBoundary, 'allowed_access' | 'prohibited_access'> & {
  allowed_access_text: string
  prohibited_access_text: string
}

type ExternalTransmissionDraft = Omit<SoulExternalTransmissionRule, 'applies_to' | 'allowed_destinations'> & {
  applies_to_text: string
  allowed_destinations_text: string
}

interface GovernanceDraft {
  risk_overrides: SoulRiskOverride[]
  trust_ceilings: SoulTrustCeiling[]
  connector_policies: ConnectorPolicyDraft[]
  data_boundaries: DataBoundaryDraft[]
  external_transmission_rules: ExternalTransmissionDraft[]
  kill_switch_rules: SoulKillSwitchRule[]
}

interface GovernanceSections {
  risk_overrides: SoulRiskOverride[]
  trust_ceilings: SoulTrustCeiling[]
  connector_policies: Record<string, SoulConnectorPolicy>
  data_boundaries: SoulDataBoundary[]
  external_transmission_rules: SoulExternalTransmissionRule[]
  kill_switch_rules: SoulKillSwitchRule[]
}

const SOUL_TEST_SCOPES = ['workspace', 'outside_workspace', 'network', 'external', 'system', 'unscoped']
const GENERIC_CAPABILITIES = new Set([
  'classify_intent',
  'summarize',
  'rag_rewrite',
  'extract_json',
  'redact',
  'health_check',
  'deploy',
  'delete',
  'credential_rotation',
  'system_configuration',
])

interface SoulTestPreset {
  label: string
  capability: string
  scope: string
  expected: SoulEvaluateResponse['decision']
  target?: string
}

function decisionBadgeClass(decision: SoulEvaluateResponse['decision']) {
  if (decision === 'blocked') return 'bg-state-error/15 text-state-error border-state-error/25'
  if (decision === 'requires_approval') return 'bg-state-degraded/15 text-state-degraded border-state-degraded/25'
  return 'bg-state-healthy/15 text-state-healthy border-state-healthy/25'
}

function presetBadgeClass(expected: SoulEvaluateResponse['decision']) {
  if (expected === 'blocked') return 'border-state-error/30 text-state-error hover:bg-state-error/10'
  if (expected === 'requires_approval') return 'border-state-degraded/30 text-state-degraded hover:bg-state-degraded/10'
  return 'border-state-healthy/30 text-state-healthy hover:bg-state-healthy/10'
}

function prettifyControlName(value: string) {
  return value
    .replace(/^connector\./, '')
    .replace(/[_.*]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function uniquePresets(presets: SoulTestPreset[]) {
  const seen = new Set<string>()
  return presets.filter(preset => {
    const key = `${preset.capability}|${preset.scope}|${preset.target ?? ''}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function buildSoulTestPresets(soul: SoulDocument): SoulTestPreset[] {
  const presets: SoulTestPreset[] = []

  for (const boundary of soul.data_boundaries) {
    const allowed = boundary.allowed_access[0]
    if (allowed) {
      presets.push({
        label: `Data allow ${prettifyControlName(allowed)}`,
        capability: allowed,
        scope: 'workspace',
        expected: 'allowed',
      })
    }
    const prohibited = boundary.prohibited_access[0]
    if (prohibited) {
      presets.push({
        label: `Data block ${prettifyControlName(prohibited)}`,
        capability: prohibited,
        scope: 'workspace',
        expected: 'blocked',
      })
    }
  }

  for (const rule of soul.external_transmission_rules) {
    const capability = rule.applies_to[0]
    if (capability) {
      presets.push({
        label: `External ${prettifyControlName(capability)}`,
        capability,
        scope: 'external',
        expected: 'requires_approval',
      })
    }
  }

  for (const rule of soul.risk_overrides) {
    presets.push({
      label: `Risk ${prettifyControlName(rule.capability)}`,
      capability: rule.capability.replace('*', 'sample'),
      scope: 'workspace',
      expected: rule.min_tier === 'T3' ? 'requires_approval' : 'allowed',
    })
  }

  for (const rule of soul.kill_switch_rules) {
    presets.push({
      label: `Kill ${prettifyControlName(rule.name)}`,
      capability: rule.trigger,
      scope: 'workspace',
      expected: 'blocked',
    })
  }

  const domainApproval = soul.autonomy_posture.requires_approval
    .filter(capability => !GENERIC_CAPABILITIES.has(capability))
    .slice(0, 4)
  for (const capability of domainApproval) {
    presets.push({
      label: `Approve ${prettifyControlName(capability)}`,
      capability,
      scope: 'workspace',
      expected: 'requires_approval',
    })
  }

  const domainAllowed = soul.autonomy_posture.allowed_autonomous
    .filter(capability => !GENERIC_CAPABILITIES.has(capability))
    .slice(0, 4)
  for (const capability of domainAllowed) {
    presets.push({
      label: `Allow ${prettifyControlName(capability)}`,
      capability,
      scope: 'workspace',
      expected: 'allowed',
    })
  }

  return uniquePresets(presets).slice(0, 12)
}

function SoulBehaviorTester({ soul }: { soul: SoulDocument }) {
  const presets = buildSoulTestPresets(soul)
  const [capability, setCapability] = useState(presets[0]?.capability ?? 'classify_intent')
  const [scope, setScope] = useState('workspace')
  const [target, setTarget] = useState('')
  const [result, setResult] = useState<SoulEvaluateResponse | null>(null)
  const [contractCases, setContractCases] = useState<SoulBehaviorContractCase[]>([])
  const [contractVersion, setContractVersion] = useState('')
  const [contractRun, setContractRun] = useState<SoulBehaviorContractRunResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [contractLoading, setContractLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [contractMessage, setContractMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  useEffect(() => {
    if (!capability.trim() && presets[0]) {
      setCapability(presets[0].capability)
      setScope(presets[0].scope)
      setTarget(presets[0].target ?? '')
    }
  }, [capability, presets])

  useEffect(() => {
    let cancelled = false
    setContractLoading(true)
    fetchSoulBehaviorContract()
      .then(contract => {
        if (cancelled) return
        setContractVersion(contract.version)
        setContractCases(contract.cases)
        setContractRun(null)
      })
      .catch(err => {
        if (!cancelled) {
          setContractMessage({ type: 'error', text: err instanceof Error ? err.message : 'Failed to load behavior contract.' })
        }
      })
      .finally(() => {
        if (!cancelled) setContractLoading(false)
      })
    return () => { cancelled = true }
  }, [soul.version])

  const runEvaluation = async (
    event?: React.FormEvent,
    override?: { capability: string; scope: string; target?: string },
  ) => {
    event?.preventDefault()
    const activeCapability = override?.capability ?? capability
    const activeScope = override?.scope ?? scope
    const activeTarget = override?.target ?? target
    const trimmed = activeCapability.trim()
    if (!trimmed) {
      setError('Capability is required.')
      setResult(null)
      return
    }

    setLoading(true)
    setError(null)
    try {
      const next = await evaluateSoulCapability(trimmed, activeScope, activeTarget.trim() || undefined)
      setResult(next)
    } catch (err: unknown) {
      setResult(null)
      setError(err instanceof Error ? err.message : 'Evaluation failed.')
    } finally {
      setLoading(false)
    }
  }

  const runPreset = (preset: SoulTestPreset) => {
    setCapability(preset.capability)
    setScope(preset.scope)
    setTarget(preset.target ?? '')
    void runEvaluation(undefined, preset)
  }

  const updateContractCase = (index: number, patch: Partial<SoulBehaviorContractCase>) => {
    setContractCases(prev => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)))
    setContractRun(null)
  }

  const addContractCase = (seed?: Partial<SoulBehaviorContractCase>) => {
    setContractCases(prev => [
      ...prev,
      {
        id: seed?.id || `local-${crypto.randomUUID?.() ?? Date.now()}`,
        label: seed?.label || 'New behavior test',
        capability: seed?.capability || capability.trim() || 'classify_intent',
        scope: seed?.scope || scope,
        target: seed?.target || null,
        expected: seed?.expected || result?.decision || 'allowed',
      },
    ])
    setContractRun(null)
  }

  const addCurrentAsContractCase = () => {
    addContractCase({
      label: result ? `${result.decision.replace('_', ' ')} ${prettifyControlName(result.capability)}` : `Expect ${prettifyControlName(capability)}`,
      capability,
      scope,
      target: target.trim() || null,
      expected: result?.decision || 'allowed',
    })
  }

  const removeContractCase = (index: number) => {
    setContractCases(prev => prev.filter((_, i) => i !== index))
    setContractRun(null)
  }

  const saveContract = async () => {
    setContractLoading(true)
    setContractMessage(null)
    try {
      const saved = await saveSoulBehaviorContract(contractCases)
      setContractVersion(saved.version)
      setContractCases(saved.cases)
      setContractMessage({ type: 'success', text: `Saved ${saved.cases.length} behavior test${saved.cases.length === 1 ? '' : 's'}.` })
    } catch (err: unknown) {
      setContractMessage({ type: 'error', text: err instanceof Error ? err.message : 'Failed to save behavior contract.' })
    } finally {
      setContractLoading(false)
    }
  }

  const runContract = async () => {
    setContractLoading(true)
    setContractMessage(null)
    try {
      const saved = await saveSoulBehaviorContract(contractCases)
      setContractCases(saved.cases)
      const run = await runSoulBehaviorContract()
      setContractRun(run)
      setContractMessage({
        type: run.failed ? 'error' : 'success',
        text: `${run.passed}/${run.count} behavior tests passed.`,
      })
    } catch (err: unknown) {
      setContractMessage({ type: 'error', text: err instanceof Error ? err.message : 'Failed to run behavior contract.' })
    } finally {
      setContractLoading(false)
    }
  }

  const runResultById = new Map((contractRun?.results ?? []).map(item => [item.id, item]))

  return (
    <Section title="Test This Soul">
      <form onSubmit={runEvaluation} className="space-y-3">
        {presets.length > 0 && (
          <div className="space-y-2">
            <p className={LABEL_CLASS}>Presets</p>
            <div className="flex flex-wrap gap-2">
              {presets.map(preset => (
                <button
                  key={`${preset.capability}-${preset.scope}-${preset.target ?? ''}`}
                  type="button"
                  onClick={() => runPreset(preset)}
                  disabled={loading}
                  className={`px-2 py-1 rounded border bg-surface-card-elevated text-[10px] transition-colors disabled:opacity-50 ${presetBadgeClass(preset.expected)}`}
                  title={`${preset.capability} -> ${preset.expected.replace('_', ' ')}`}
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-[1fr_160px_1fr_auto] gap-2 items-end">
          <FieldLabel label="Capability">
            <input
              value={capability}
              onChange={event => setCapability(event.target.value)}
              className={FIELD_CLASS}
              placeholder="scan_transactions"
            />
          </FieldLabel>
          <FieldLabel label="Scope">
            <select value={scope} onChange={event => setScope(event.target.value)} className={FIELD_CLASS}>
              {SOUL_TEST_SCOPES.map(option => <option key={option} value={option}>{option}</option>)}
            </select>
          </FieldLabel>
          <FieldLabel label="Target">
            <input
              value={target}
              onChange={event => setTarget(event.target.value)}
              className={FIELD_CLASS}
              placeholder="optional"
            />
          </FieldLabel>
          <button
            type="submit"
            disabled={loading}
            className="px-3 py-1.5 text-[11px] font-medium rounded bg-accent-primary text-white hover:bg-accent-primary/80 transition-colors disabled:opacity-50"
          >
            {loading ? 'Testing...' : 'Evaluate'}
          </button>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={addCurrentAsContractCase}
            className="px-3 py-1.5 text-[11px] rounded bg-surface-input text-text-secondary hover:text-text-primary transition-colors"
          >
            Add Current Test
          </button>
          <button
            type="button"
            onClick={() => addContractCase()}
            className="px-3 py-1.5 text-[11px] rounded bg-surface-input text-text-secondary hover:text-text-primary transition-colors"
          >
            Add Blank Test
          </button>
        </div>

        {error && (
          <div className="p-2 rounded border border-state-error/30 bg-state-error/10 text-[11px] text-state-error">
            {error}
          </div>
        )}

        {result && (
          <div className="rounded border border-border-default/50 bg-surface-card-elevated p-3 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`px-2 py-0.5 rounded border text-[10px] font-semibold uppercase tracking-wider ${decisionBadgeClass(result.decision)}`}>
                {result.decision.replace('_', ' ')}
              </span>
              <span className="px-2 py-0.5 rounded bg-surface-input text-[10px] font-mono text-text-secondary">
                {result.risk_tier}
              </span>
              {result.requires_sync_verify && (
                <span className="px-2 py-0.5 rounded bg-accent-primary/10 text-[10px] text-accent-primary">
                  sync verify
                </span>
              )}
            </div>
            {result.reasons.length > 0 && (
              <div className="space-y-1">
                <p className={LABEL_CLASS}>Reasons</p>
                {result.reasons.map((reason, i) => (
                  <p key={i} className="text-xs text-text-secondary leading-relaxed">{reason}</p>
                ))}
              </div>
            )}
            {result.matched_controls.length > 0 && (
              <div className="space-y-1">
                <p className={LABEL_CLASS}>Matched Controls</p>
                <TagList items={result.matched_controls} color={result.blocked ? 'state-error' : result.requires_approval ? 'state-degraded' : 'state-healthy'} />
              </div>
            )}
          </div>
        )}

        <div className="rounded border border-border-default/50 bg-surface-card-elevated p-3 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-medium text-text-secondary uppercase tracking-wider">Behavior Contract</p>
              <p className="text-[11px] text-text-muted mt-0.5">
                {contractVersion ? `Active Soul ${contractVersion}` : 'Active Soul'} · {contractCases.length} expected behavior test{contractCases.length === 1 ? '' : 's'}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={saveContract}
                disabled={contractLoading}
                className="px-3 py-1.5 text-[11px] rounded bg-surface-input text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
              >
                Save Contract
              </button>
              <button
                type="button"
                onClick={runContract}
                disabled={contractLoading || contractCases.length === 0}
                className="px-3 py-1.5 text-[11px] font-medium rounded bg-accent-primary text-white hover:bg-accent-primary/80 transition-colors disabled:opacity-50"
              >
                {contractLoading ? 'Working...' : 'Run All'}
              </button>
            </div>
          </div>

          {contractMessage && (
            <div className={`p-2 rounded border text-[11px] ${
              contractMessage.type === 'success'
                ? 'bg-state-healthy/10 border-state-healthy/30 text-state-healthy'
                : 'bg-state-error/10 border-state-error/30 text-state-error'
            }`}>
              {contractMessage.text}
            </div>
          )}

          {contractCases.length === 0 ? (
            <p className="text-xs text-text-muted">No behavior tests saved for this Soul version.</p>
          ) : (
            <div className="space-y-2">
              {contractCases.map((testCase, index) => {
                const runResult = runResultById.get(testCase.id)
                return (
                  <div key={testCase.id || index} className="rounded border border-border-default/50 bg-surface-card p-2 space-y-2">
                    <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_130px_auto] gap-2 items-end">
                      <FieldLabel label="Label">
                        <input value={testCase.label} onChange={event => updateContractCase(index, { label: event.target.value })} className={FIELD_CLASS} />
                      </FieldLabel>
                      <FieldLabel label="Capability">
                        <input value={testCase.capability} onChange={event => updateContractCase(index, { capability: event.target.value })} className={FIELD_CLASS} />
                      </FieldLabel>
                      <FieldLabel label="Expected">
                        <select value={testCase.expected} onChange={event => updateContractCase(index, { expected: event.target.value as SoulBehaviorContractCase['expected'] })} className={FIELD_CLASS}>
                          <option value="allowed">allowed</option>
                          <option value="requires_approval">requires approval</option>
                          <option value="blocked">blocked</option>
                        </select>
                      </FieldLabel>
                      <button
                        type="button"
                        onClick={() => removeContractCase(index)}
                        className="px-2 py-1.5 text-[10px] rounded bg-state-error/10 text-state-error hover:bg-state-error/20 transition-colors"
                      >
                        Remove
                      </button>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      <FieldLabel label="Scope">
                        <select value={testCase.scope} onChange={event => updateContractCase(index, { scope: event.target.value })} className={FIELD_CLASS}>
                          {SOUL_TEST_SCOPES.map(option => <option key={option} value={option}>{option}</option>)}
                        </select>
                      </FieldLabel>
                      <FieldLabel label="Target">
                        <input value={testCase.target ?? ''} onChange={event => updateContractCase(index, { target: event.target.value || null })} className={FIELD_CLASS} />
                      </FieldLabel>
                    </div>
                    {runResult && (
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`px-2 py-0.5 rounded border text-[10px] font-semibold ${runResult.passed ? 'bg-state-healthy/10 border-state-healthy/30 text-state-healthy' : 'bg-state-error/10 border-state-error/30 text-state-error'}`}>
                          {runResult.passed ? 'PASS' : 'FAIL'}
                        </span>
                        <span className={`px-2 py-0.5 rounded border text-[10px] ${decisionBadgeClass(runResult.decision)}`}>
                          actual {runResult.decision.replace('_', ' ')}
                        </span>
                        <span className="text-[10px] text-text-muted font-mono">{runResult.risk_tier}</span>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </form>
    </Section>
  )
}

function listToText(items?: string[]) {
  return (items ?? []).join('\n')
}

function textToList(text: string) {
  return text
    .split(/[\n,]/)
    .map(item => item.trim())
    .filter(Boolean)
}

function governanceDraftFromSoul(soul: SoulDocument): GovernanceDraft {
  return {
    risk_overrides: (soul.risk_overrides ?? []).map(rule => ({ ...rule })),
    trust_ceilings: (soul.trust_ceilings ?? []).map(rule => ({ ...rule })),
    connector_policies: Object.entries(soul.connector_policies ?? {}).map(([connector, policy]) => ({
      connector,
      verified_recipients_text: listToText(policy.verified_recipients),
      allowed_channels_text: listToText(policy.allowed_channels),
      restrict_dm: policy.restrict_dm ?? false,
      max_sends_per_day_text: policy.max_sends_per_day == null ? '' : String(policy.max_sends_per_day),
      require_content_verification: policy.require_content_verification ?? false,
      pii_scrubbing_required: policy.pii_scrubbing_required ?? false,
      approval_required_for_send: policy.approval_required_for_send ?? false,
    })),
    data_boundaries: (soul.data_boundaries ?? []).map(boundary => ({
      name: boundary.name,
      classification: boundary.classification,
      allowed_access_text: listToText(boundary.allowed_access),
      prohibited_access_text: listToText(boundary.prohibited_access),
      external_transmission_allowed: boundary.external_transmission_allowed ?? false,
      bulk_export_requires_approval: boundary.bulk_export_requires_approval ?? true,
      reason: boundary.reason ?? '',
    })),
    external_transmission_rules: (soul.external_transmission_rules ?? []).map(rule => ({
      name: rule.name,
      applies_to_text: listToText(rule.applies_to),
      requires_approval_tier: rule.requires_approval_tier ?? 'T3',
      pii_scrubbing_required: rule.pii_scrubbing_required ?? true,
      allowed_destinations_text: listToText(rule.allowed_destinations),
      reason: rule.reason ?? '',
    })),
    kill_switch_rules: (soul.kill_switch_rules ?? []).map(rule => ({ ...rule })),
  }
}

function validateAndBuildGovernanceSections(draft: GovernanceDraft): { sections?: GovernanceSections; error?: string } {
  const risk_overrides = draft.risk_overrides.map(rule => ({
    capability: rule.capability.trim(),
    min_tier: rule.min_tier,
    reason: rule.reason.trim(),
  }))
  if (risk_overrides.some(rule => !rule.capability || !rule.reason)) {
    return { error: 'Risk overrides require capability and reason.' }
  }

  const trust_ceilings = draft.trust_ceilings.map(rule => ({
    capability: rule.capability.trim(),
    max_graduation: rule.max_graduation,
    reason: rule.reason.trim(),
  }))
  if (trust_ceilings.some(rule => !rule.capability || !rule.reason)) {
    return { error: 'Trust ceilings require capability and reason.' }
  }

  const connector_policies: Record<string, SoulConnectorPolicy> = {}
  for (const policy of draft.connector_policies) {
    const connector = policy.connector.trim()
    if (!connector) return { error: 'Connector policies require a connector id.' }
    const maxSends = policy.max_sends_per_day_text.trim()
    const parsedMaxSends = maxSends === '' ? null : Number(maxSends)
    if (parsedMaxSends !== null && (!Number.isInteger(parsedMaxSends) || parsedMaxSends < 0)) {
      return { error: 'Max sends per day must be a non-negative whole number.' }
    }
    connector_policies[connector] = {
      verified_recipients: textToList(policy.verified_recipients_text),
      allowed_channels: textToList(policy.allowed_channels_text),
      restrict_dm: policy.restrict_dm,
      max_sends_per_day: parsedMaxSends,
      require_content_verification: policy.require_content_verification,
      pii_scrubbing_required: policy.pii_scrubbing_required,
      approval_required_for_send: policy.approval_required_for_send,
    }
  }

  const data_boundaries = draft.data_boundaries.map(boundary => ({
    name: boundary.name.trim(),
    classification: boundary.classification.trim(),
    allowed_access: textToList(boundary.allowed_access_text),
    prohibited_access: textToList(boundary.prohibited_access_text),
    external_transmission_allowed: boundary.external_transmission_allowed,
    bulk_export_requires_approval: boundary.bulk_export_requires_approval,
    reason: boundary.reason.trim(),
  }))
  if (data_boundaries.some(boundary => !boundary.name || !boundary.classification)) {
    return { error: 'Data boundaries require name and classification.' }
  }

  const external_transmission_rules = draft.external_transmission_rules.map(rule => ({
    name: rule.name.trim(),
    applies_to: textToList(rule.applies_to_text),
    requires_approval_tier: rule.requires_approval_tier,
    pii_scrubbing_required: rule.pii_scrubbing_required,
    allowed_destinations: textToList(rule.allowed_destinations_text),
    reason: rule.reason.trim(),
  }))
  if (external_transmission_rules.some(rule => !rule.name)) {
    return { error: 'External transmission rules require a name.' }
  }

  const kill_switch_rules = draft.kill_switch_rules.map(rule => ({
    name: rule.name.trim(),
    trigger: rule.trigger.trim(),
    action: rule.action.trim() || 'halt_and_escalate',
    enforced: rule.enforced,
    reason: rule.reason.trim(),
  }))
  if (kill_switch_rules.some(rule => !rule.name || !rule.trigger)) {
    return { error: 'Kill switch rules require name and trigger.' }
  }

  return {
    sections: {
      risk_overrides,
      trust_ceilings,
      connector_policies,
      data_boundaries,
      external_transmission_rules,
      kill_switch_rules,
    },
  }
}

function isYamlInlineValue(value: unknown) {
  if (value === null || typeof value !== 'object') return true
  if (Array.isArray(value)) return value.length === 0
  return Object.keys(value).length === 0
}

function yamlScalar(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(String(value))
}

function renderYamlValue(value: unknown, indent = 0): string {
  const pad = ' '.repeat(indent)
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]'
    return value.map(item => (
      isYamlInlineValue(item)
        ? `${pad}- ${renderYamlValue(item, 0)}`
        : `${pad}-\n${renderYamlValue(item, indent + 2)}`
    )).join('\n')
  }
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return '{}'
    return entries.map(([key, entryValue]) => (
      isYamlInlineValue(entryValue)
        ? `${pad}${key}: ${renderYamlValue(entryValue, 0)}`
        : `${pad}${key}:\n${renderYamlValue(entryValue, indent + 2)}`
    )).join('\n')
  }
  return yamlScalar(value)
}

function sectionHasContent(value: unknown) {
  if (Array.isArray(value)) return value.length > 0
  return Boolean(value && typeof value === 'object' && Object.keys(value).length > 0)
}

function renderGovernanceSections(sections: GovernanceSections) {
  return GOVERNANCE_SECTION_KEYS
    .filter(key => sectionHasContent(sections[key]))
    .map(key => `${key}:\n${renderYamlValue(sections[key], 2)}`)
    .join('\n')
}

function topLevelYamlKey(line: string) {
  if (/^\s/.test(line) || line.trim() === '' || line.trimStart().startsWith('#')) return null
  return /^([A-Za-z_][\w]*):(?:\s.*)?$/.exec(line)?.[1] ?? null
}

function composeGovernanceProposalYaml(rawYaml: string, sections: GovernanceSections) {
  const lines = rawYaml.replace(/\r\n/g, '\n').split('\n')
  const kept: string[] = []
  let skippingGovernanceSection = false

  for (const line of lines) {
    const key = topLevelYamlKey(line)
    if (key && GOVERNANCE_SECTION_SET.has(key)) {
      skippingGovernanceSection = true
      continue
    }
    if (skippingGovernanceSection && key) {
      skippingGovernanceSection = false
    }
    if (!skippingGovernanceSection) kept.push(line)
  }

  const baseYaml = kept.join('\n').replace(/\s+$/g, '')
  const governanceYaml = renderGovernanceSections(sections)
  return governanceYaml ? `${baseYaml}\n\n${governanceYaml}\n` : `${baseYaml}\n`
}

function FieldLabel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="space-y-1">
      <span className={LABEL_CLASS}>{label}</span>
      {children}
    </label>
  )
}

function CheckboxField({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-xs text-text-primary">
      <input
        type="checkbox"
        checked={checked}
        onChange={event => onChange(event.target.checked)}
        className="h-3.5 w-3.5 rounded border-border-default bg-surface-input"
      />
      <span>{label}</span>
    </label>
  )
}

function TierSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <select value={value} onChange={event => onChange(event.target.value)} className={FIELD_CLASS}>
      {TIER_OPTIONS.map(tier => <option key={tier} value={tier}>{tier}</option>)}
    </select>
  )
}

function SoulViewer({ soul, rawYaml, disabled, onProposed }: { soul: SoulDocument; rawYaml: string; disabled: boolean; onProposed: () => void }) {
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

      <SoulBehaviorTester soul={soul} />

      <GovernanceControlsEditor
        soul={soul}
        rawYaml={rawYaml}
        disabled={disabled}
        onProposed={onProposed}
      />
    </div>
  )
}

// ── YAML Editor ─────────────────────────────────────────────────────
function ControlGroup({ title, count, onAdd, children }: { title: string; count: number; onAdd: () => void; children: React.ReactNode }) {
  return (
    <div className="space-y-2 border-t border-border-default/50 pt-3 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between gap-3">
        <h5 className="text-[10px] text-text-secondary uppercase tracking-wider">{title} ({count})</h5>
        <button
          type="button"
          onClick={onAdd}
          className="px-2 py-1 text-[10px] rounded bg-surface-input text-text-secondary hover:text-text-primary transition-colors"
        >
          Add
        </button>
      </div>
      {children}
    </div>
  )
}

function ReadOnlyControlGroup({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <div className="space-y-2 border-t border-border-default/50 pt-3 first:border-t-0 first:pt-0">
      <h5 className="text-[10px] text-text-secondary uppercase tracking-wider">{title} ({count})</h5>
      {count === 0 ? (
        <p className="text-[11px] text-text-muted">No entries configured.</p>
      ) : children}
    </div>
  )
}

function DetailKV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className={LABEL_CLASS}>{label}</p>
      <div className="mt-1 text-xs text-text-primary">{children}</div>
    </div>
  )
}

function BooleanStatus({ value }: { value: boolean }) {
  return (
    <span className={`text-[11px] font-semibold ${value ? 'text-state-healthy' : 'text-text-muted'}`}>
      {value ? 'YES' : 'NO'}
    </span>
  )
}

function ReadOnlyGovernanceDetails({ soul }: { soul: SoulDocument }) {
  const connectorPolicies = Object.entries(soul.connector_policies ?? {})

  return (
    <div className="space-y-4">
      <ReadOnlyControlGroup title="Risk Overrides" count={soul.risk_overrides?.length ?? 0}>
        <div className="space-y-2">
          {(soul.risk_overrides ?? []).map((rule, i) => (
            <div key={`${rule.capability}-${i}`} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-mono text-text-primary">{rule.capability}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-state-degraded/15 text-state-degraded">{rule.min_tier}</span>
              </div>
              <p className="text-[11px] text-text-secondary leading-relaxed">{rule.reason}</p>
            </div>
          ))}
        </div>
      </ReadOnlyControlGroup>

      <ReadOnlyControlGroup title="Trust Ceilings" count={soul.trust_ceilings?.length ?? 0}>
        <div className="space-y-2">
          {(soul.trust_ceilings ?? []).map((rule, i) => (
            <div key={`${rule.capability}-${i}`} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-mono text-text-primary">{rule.capability}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-primary/15 text-accent-primary">max {rule.max_graduation}</span>
              </div>
              <p className="text-[11px] text-text-secondary leading-relaxed">{rule.reason}</p>
            </div>
          ))}
        </div>
      </ReadOnlyControlGroup>

      <ReadOnlyControlGroup title="Connector Policies" count={connectorPolicies.length}>
        <div className="space-y-2">
          {connectorPolicies.map(([connector, policy]) => (
            <div key={connector} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-mono text-text-primary">{connector}</span>
                {policy.max_sends_per_day != null && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-input text-text-secondary">{policy.max_sends_per_day}/day</span>
                )}
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <DetailKV label="Verified Recipients">
                  {policy.verified_recipients?.length ? <TagList items={policy.verified_recipients} /> : <span className="text-text-muted">None</span>}
                </DetailKV>
                <DetailKV label="Allowed Channels">
                  {policy.allowed_channels?.length ? <TagList items={policy.allowed_channels} /> : <span className="text-text-muted">None</span>}
                </DetailKV>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <DetailKV label="Restrict DMs"><BooleanStatus value={policy.restrict_dm} /></DetailKV>
                <DetailKV label="Verify Content"><BooleanStatus value={policy.require_content_verification} /></DetailKV>
                <DetailKV label="Scrub PII"><BooleanStatus value={policy.pii_scrubbing_required} /></DetailKV>
                <DetailKV label="Approve Sends"><BooleanStatus value={policy.approval_required_for_send} /></DetailKV>
              </div>
            </div>
          ))}
        </div>
      </ReadOnlyControlGroup>

      <ReadOnlyControlGroup title="Data Boundaries" count={soul.data_boundaries?.length ?? 0}>
        <div className="space-y-2">
          {(soul.data_boundaries ?? []).map((boundary, i) => (
            <div key={`${boundary.name}-${i}`} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-mono text-text-primary">{boundary.name}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-input text-text-secondary">{boundary.classification}</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <DetailKV label="Allowed Access">
                  {boundary.allowed_access?.length ? <TagList items={boundary.allowed_access} color="state-healthy" /> : <span className="text-text-muted">None</span>}
                </DetailKV>
                <DetailKV label="Prohibited Access">
                  {boundary.prohibited_access?.length ? <TagList items={boundary.prohibited_access} color="state-error" /> : <span className="text-text-muted">None</span>}
                </DetailKV>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <DetailKV label="External Transmission"><BooleanStatus value={boundary.external_transmission_allowed} /></DetailKV>
                <DetailKV label="Bulk Export Approval"><BooleanStatus value={boundary.bulk_export_requires_approval} /></DetailKV>
              </div>
              {boundary.reason && <p className="text-[11px] text-text-secondary leading-relaxed">{boundary.reason}</p>}
            </div>
          ))}
        </div>
      </ReadOnlyControlGroup>

      <ReadOnlyControlGroup title="External Transmission Rules" count={soul.external_transmission_rules?.length ?? 0}>
        <div className="space-y-2">
          {(soul.external_transmission_rules ?? []).map((rule, i) => (
            <div key={`${rule.name}-${i}`} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-mono text-text-primary">{rule.name}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-state-degraded/15 text-state-degraded">{rule.requires_approval_tier}</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <DetailKV label="Applies To">
                  {rule.applies_to?.length ? <TagList items={rule.applies_to} /> : <span className="text-text-muted">None</span>}
                </DetailKV>
                <DetailKV label="Allowed Destinations">
                  {rule.allowed_destinations?.length ? <TagList items={rule.allowed_destinations} /> : <span className="text-text-muted">None</span>}
                </DetailKV>
              </div>
              <DetailKV label="PII Scrubbing"><BooleanStatus value={rule.pii_scrubbing_required} /></DetailKV>
              {rule.reason && <p className="text-[11px] text-text-secondary leading-relaxed">{rule.reason}</p>}
            </div>
          ))}
        </div>
      </ReadOnlyControlGroup>

      <ReadOnlyControlGroup title="Kill Switch Rules" count={soul.kill_switch_rules?.length ?? 0}>
        <div className="space-y-2">
          {(soul.kill_switch_rules ?? []).map((rule, i) => (
            <div key={`${rule.name}-${i}`} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-mono text-text-primary">{rule.name}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${rule.enforced ? 'bg-state-healthy/15 text-state-healthy' : 'bg-state-error/15 text-state-error'}`}>
                  {rule.enforced ? 'ENFORCED' : 'DISABLED'}
                </span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <DetailKV label="Trigger"><span className="font-mono text-[11px]">{rule.trigger}</span></DetailKV>
                <DetailKV label="Action"><span className="font-mono text-[11px]">{rule.action}</span></DetailKV>
              </div>
              {rule.reason && <p className="text-[11px] text-text-secondary leading-relaxed">{rule.reason}</p>}
            </div>
          ))}
        </div>
      </ReadOnlyControlGroup>
    </div>
  )
}

function GovernanceControlsEditor({ soul, rawYaml, disabled, onProposed }: { soul: SoulDocument; rawYaml: string; disabled: boolean; onProposed: () => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<GovernanceDraft>(() => governanceDraftFromSoul(soul))
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  useEffect(() => {
    setDraft(governanceDraftFromSoul(soul))
    setEditing(false)
    setResult(null)
  }, [soul])

  const baseline = governanceDraftFromSoul(soul)
  const hasChanges = JSON.stringify(draft) !== JSON.stringify(baseline)

  const updateList = <K extends keyof GovernanceDraft>(
    key: K,
    index: number,
    patch: Partial<GovernanceDraft[K][number]>,
  ) => {
    setDraft(prev => ({
      ...prev,
      [key]: prev[key].map((item, i) => (i === index ? { ...item, ...patch } : item)),
    }))
  }

  const addListItem = <K extends keyof GovernanceDraft>(key: K, item: GovernanceDraft[K][number]) => {
    setDraft(prev => ({ ...prev, [key]: [...prev[key], item] }))
  }

  const removeListItem = <K extends keyof GovernanceDraft>(key: K, index: number) => {
    setDraft(prev => ({ ...prev, [key]: prev[key].filter((_, i) => i !== index) }))
  }

  const handlePropose = async () => {
    const built = validateAndBuildGovernanceSections(draft)
    if (built.error || !built.sections) {
      setResult({ type: 'error', message: built.error ?? 'Invalid governance controls.' })
      return
    }

    setSaving(true)
    setResult(null)
    try {
      const proposedYaml = composeGovernanceProposalYaml(rawYaml, built.sections)
      const res = await proposeSoulAmendment(proposedYaml)
      setResult({
        type: 'success',
        message: `Proposal ${res.proposal_id} created (${res.diff_summary.length} change${res.diff_summary.length !== 1 ? 's' : ''}). Go to Pending Proposals to approve and activate.`,
      })
      setEditing(false)
      onProposed()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to create proposal'
      setResult({ type: 'error', message: msg })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section title="Structured Governance Controls">
      <div className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
          {[
            ['Risk', soul.risk_overrides?.length ?? 0],
            ['Trust', soul.trust_ceilings?.length ?? 0],
            ['Connectors', Object.keys(soul.connector_policies ?? {}).length],
            ['Data', soul.data_boundaries?.length ?? 0],
            ['External', soul.external_transmission_rules?.length ?? 0],
            ['Kill', soul.kill_switch_rules?.length ?? 0],
          ].map(([label, count]) => (
            <div key={label} className="rounded border border-border-default/50 bg-surface-card-elevated px-2 py-1.5">
              <p className="text-[9px] text-text-muted uppercase tracking-wider">{label}</p>
              <p className="text-sm font-mono text-text-primary">{count}</p>
            </div>
          ))}
        </div>

        {!editing ? (
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setEditing(true)}
                disabled={disabled}
                className={`px-3 py-1.5 text-[11px] font-medium rounded transition-colors ${
                  disabled
                    ? 'bg-surface-input text-text-muted cursor-not-allowed'
                    : 'bg-accent-primary text-white hover:bg-accent-primary/80'
                }`}
              >
                Edit Controls
              </button>
              {disabled && <span className="text-[11px] text-text-muted">Disabled during Crusader Mode.</span>}
            </div>
            <ReadOnlyGovernanceDetails soul={soul} />
          </div>
        ) : (
          <div className="space-y-4">
            <ControlGroup title="Risk Overrides" count={draft.risk_overrides.length} onAdd={() => addListItem('risk_overrides', { capability: '', min_tier: 'T3', reason: '' })}>
              <div className="space-y-2">
                {draft.risk_overrides.map((rule, i) => (
                  <div key={i} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-[1fr_120px_auto] gap-2 items-end">
                      <FieldLabel label="Capability"><input value={rule.capability} onChange={event => updateList('risk_overrides', i, { capability: event.target.value })} className={FIELD_CLASS} /></FieldLabel>
                      <FieldLabel label="Min Tier"><TierSelect value={rule.min_tier} onChange={value => updateList('risk_overrides', i, { min_tier: value })} /></FieldLabel>
                      <button type="button" onClick={() => removeListItem('risk_overrides', i)} className="px-2 py-1.5 text-[10px] rounded bg-state-error/10 text-state-error hover:bg-state-error/20 transition-colors">Remove</button>
                    </div>
                    <FieldLabel label="Reason"><textarea value={rule.reason} onChange={event => updateList('risk_overrides', i, { reason: event.target.value })} rows={2} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                  </div>
                ))}
              </div>
            </ControlGroup>

            <ControlGroup title="Trust Ceilings" count={draft.trust_ceilings.length} onAdd={() => addListItem('trust_ceilings', { capability: '', max_graduation: 'T2', reason: '' })}>
              <div className="space-y-2">
                {draft.trust_ceilings.map((rule, i) => (
                  <div key={i} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-[1fr_120px_auto] gap-2 items-end">
                      <FieldLabel label="Capability"><input value={rule.capability} onChange={event => updateList('trust_ceilings', i, { capability: event.target.value })} className={FIELD_CLASS} /></FieldLabel>
                      <FieldLabel label="Max Graduation"><TierSelect value={rule.max_graduation} onChange={value => updateList('trust_ceilings', i, { max_graduation: value })} /></FieldLabel>
                      <button type="button" onClick={() => removeListItem('trust_ceilings', i)} className="px-2 py-1.5 text-[10px] rounded bg-state-error/10 text-state-error hover:bg-state-error/20 transition-colors">Remove</button>
                    </div>
                    <FieldLabel label="Reason"><textarea value={rule.reason} onChange={event => updateList('trust_ceilings', i, { reason: event.target.value })} rows={2} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                  </div>
                ))}
              </div>
            </ControlGroup>

            <ControlGroup title="Connector Policies" count={draft.connector_policies.length} onAdd={() => addListItem('connector_policies', { connector: '', verified_recipients_text: '', allowed_channels_text: '', restrict_dm: false, max_sends_per_day_text: '', require_content_verification: false, pii_scrubbing_required: false, approval_required_for_send: false })}>
              <div className="space-y-2">
                {draft.connector_policies.map((policy, i) => (
                  <div key={i} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-[1fr_160px_auto] gap-2 items-end">
                      <FieldLabel label="Connector"><input value={policy.connector} onChange={event => updateList('connector_policies', i, { connector: event.target.value })} className={FIELD_CLASS} /></FieldLabel>
                      <FieldLabel label="Max Sends/Day"><input type="number" min={0} value={policy.max_sends_per_day_text} onChange={event => updateList('connector_policies', i, { max_sends_per_day_text: event.target.value })} className={FIELD_CLASS} /></FieldLabel>
                      <button type="button" onClick={() => removeListItem('connector_policies', i)} className="px-2 py-1.5 text-[10px] rounded bg-state-error/10 text-state-error hover:bg-state-error/20 transition-colors">Remove</button>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      <FieldLabel label="Verified Recipients"><textarea value={policy.verified_recipients_text} onChange={event => updateList('connector_policies', i, { verified_recipients_text: event.target.value })} rows={3} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                      <FieldLabel label="Allowed Channels"><textarea value={policy.allowed_channels_text} onChange={event => updateList('connector_policies', i, { allowed_channels_text: event.target.value })} rows={3} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      <CheckboxField label="Restrict DMs" checked={policy.restrict_dm} onChange={checked => updateList('connector_policies', i, { restrict_dm: checked })} />
                      <CheckboxField label="Require Content Verification" checked={policy.require_content_verification} onChange={checked => updateList('connector_policies', i, { require_content_verification: checked })} />
                      <CheckboxField label="PII Scrubbing Required" checked={policy.pii_scrubbing_required} onChange={checked => updateList('connector_policies', i, { pii_scrubbing_required: checked })} />
                      <CheckboxField label="Approval Required For Send" checked={policy.approval_required_for_send} onChange={checked => updateList('connector_policies', i, { approval_required_for_send: checked })} />
                    </div>
                  </div>
                ))}
              </div>
            </ControlGroup>

            <ControlGroup title="Data Boundaries" count={draft.data_boundaries.length} onAdd={() => addListItem('data_boundaries', { name: '', classification: '', allowed_access_text: '', prohibited_access_text: '', external_transmission_allowed: false, bulk_export_requires_approval: true, reason: '' })}>
              <div className="space-y-2">
                {draft.data_boundaries.map((boundary, i) => (
                  <div key={i} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto] gap-2 items-end">
                      <FieldLabel label="Name"><input value={boundary.name} onChange={event => updateList('data_boundaries', i, { name: event.target.value })} className={FIELD_CLASS} /></FieldLabel>
                      <FieldLabel label="Classification"><input value={boundary.classification} onChange={event => updateList('data_boundaries', i, { classification: event.target.value })} className={FIELD_CLASS} /></FieldLabel>
                      <button type="button" onClick={() => removeListItem('data_boundaries', i)} className="px-2 py-1.5 text-[10px] rounded bg-state-error/10 text-state-error hover:bg-state-error/20 transition-colors">Remove</button>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      <FieldLabel label="Allowed Access"><textarea value={boundary.allowed_access_text} onChange={event => updateList('data_boundaries', i, { allowed_access_text: event.target.value })} rows={3} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                      <FieldLabel label="Prohibited Access"><textarea value={boundary.prohibited_access_text} onChange={event => updateList('data_boundaries', i, { prohibited_access_text: event.target.value })} rows={3} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      <CheckboxField label="External Transmission Allowed" checked={boundary.external_transmission_allowed} onChange={checked => updateList('data_boundaries', i, { external_transmission_allowed: checked })} />
                      <CheckboxField label="Bulk Export Requires Approval" checked={boundary.bulk_export_requires_approval} onChange={checked => updateList('data_boundaries', i, { bulk_export_requires_approval: checked })} />
                    </div>
                    <FieldLabel label="Reason"><textarea value={boundary.reason} onChange={event => updateList('data_boundaries', i, { reason: event.target.value })} rows={2} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                  </div>
                ))}
              </div>
            </ControlGroup>

            <ControlGroup title="External Transmission Rules" count={draft.external_transmission_rules.length} onAdd={() => addListItem('external_transmission_rules', { name: '', applies_to_text: '', requires_approval_tier: 'T3', pii_scrubbing_required: true, allowed_destinations_text: '', reason: '' })}>
              <div className="space-y-2">
                {draft.external_transmission_rules.map((rule, i) => (
                  <div key={i} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-[1fr_140px_auto] gap-2 items-end">
                      <FieldLabel label="Name"><input value={rule.name} onChange={event => updateList('external_transmission_rules', i, { name: event.target.value })} className={FIELD_CLASS} /></FieldLabel>
                      <FieldLabel label="Approval Tier"><TierSelect value={rule.requires_approval_tier} onChange={value => updateList('external_transmission_rules', i, { requires_approval_tier: value })} /></FieldLabel>
                      <button type="button" onClick={() => removeListItem('external_transmission_rules', i)} className="px-2 py-1.5 text-[10px] rounded bg-state-error/10 text-state-error hover:bg-state-error/20 transition-colors">Remove</button>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      <FieldLabel label="Applies To"><textarea value={rule.applies_to_text} onChange={event => updateList('external_transmission_rules', i, { applies_to_text: event.target.value })} rows={3} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                      <FieldLabel label="Allowed Destinations"><textarea value={rule.allowed_destinations_text} onChange={event => updateList('external_transmission_rules', i, { allowed_destinations_text: event.target.value })} rows={3} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                    </div>
                    <CheckboxField label="PII Scrubbing Required" checked={rule.pii_scrubbing_required} onChange={checked => updateList('external_transmission_rules', i, { pii_scrubbing_required: checked })} />
                    <FieldLabel label="Reason"><textarea value={rule.reason} onChange={event => updateList('external_transmission_rules', i, { reason: event.target.value })} rows={2} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                  </div>
                ))}
              </div>
            </ControlGroup>

            <ControlGroup title="Kill Switch Rules" count={draft.kill_switch_rules.length} onAdd={() => addListItem('kill_switch_rules', { name: '', trigger: '', action: 'halt_and_escalate', enforced: true, reason: '' })}>
              <div className="space-y-2">
                {draft.kill_switch_rules.map((rule, i) => (
                  <div key={i} className="p-3 rounded border border-border-default/50 bg-surface-card-elevated space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto] gap-2 items-end">
                      <FieldLabel label="Name"><input value={rule.name} onChange={event => updateList('kill_switch_rules', i, { name: event.target.value })} className={FIELD_CLASS} /></FieldLabel>
                      <FieldLabel label="Trigger"><input value={rule.trigger} onChange={event => updateList('kill_switch_rules', i, { trigger: event.target.value })} className={FIELD_CLASS} /></FieldLabel>
                      <button type="button" onClick={() => removeListItem('kill_switch_rules', i)} className="px-2 py-1.5 text-[10px] rounded bg-state-error/10 text-state-error hover:bg-state-error/20 transition-colors">Remove</button>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      <FieldLabel label="Action"><input value={rule.action} onChange={event => updateList('kill_switch_rules', i, { action: event.target.value })} className={FIELD_CLASS} /></FieldLabel>
                      <div className="flex items-end pb-1"><CheckboxField label="Enforced" checked={rule.enforced} onChange={checked => updateList('kill_switch_rules', i, { enforced: checked })} /></div>
                    </div>
                    <FieldLabel label="Reason"><textarea value={rule.reason} onChange={event => updateList('kill_switch_rules', i, { reason: event.target.value })} rows={2} className={`${FIELD_CLASS} resize-y`} /></FieldLabel>
                  </div>
                ))}
              </div>
            </ControlGroup>

            <div className="flex flex-wrap items-center gap-3 pt-2">
              <button
                type="button"
                onClick={handlePropose}
                disabled={saving || !hasChanges}
                className={`px-4 py-1.5 text-[11px] font-medium rounded transition-colors ${
                  hasChanges
                    ? 'bg-accent-primary text-white hover:bg-accent-primary/80'
                    : 'bg-surface-input text-text-muted cursor-not-allowed'
                }`}
              >
                {saving ? 'Proposing...' : 'Propose Governance Changes'}
              </button>
              {hasChanges && (
                <button type="button" onClick={() => { setDraft(baseline); setResult(null) }} className="px-3 py-1.5 text-[11px] text-text-muted hover:text-text-primary transition-colors">Reset</button>
              )}
              <button type="button" onClick={() => { setEditing(false); setDraft(baseline); setResult(null) }} className="px-3 py-1.5 text-[11px] text-text-muted hover:text-text-primary transition-colors">Close</button>
            </div>
          </div>
        )}

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
    </Section>
  )
}

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

// ── Template Browser ────────────────────────────────────────────────
function TemplateBrowser({ onApplied }: { onApplied: () => void }) {
  const [templates, setTemplates] = useState<SoulTemplateMetadata[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<SoulTemplateDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null)
  const [industryFilter, setIndustryFilter] = useState<string>('')

  useEffect(() => {
    setLoading(true)
    fetchSoulTemplates(industryFilter || undefined)
      .then(res => setTemplates(res.templates))
      .catch(() => setTemplates([]))
      .finally(() => setLoading(false))
  }, [industryFilter])

  const handleSelect = async (name: string) => {
    setDetailLoading(true)
    setResult(null)
    try {
      const detail = await fetchSoulTemplateDetail(name)
      setSelected(detail)
    } catch {
      setResult({ type: 'error', message: `Failed to load template: ${name}` })
    } finally {
      setDetailLoading(false)
    }
  }

  const handleApply = async () => {
    if (!selected) return
    setApplying(true)
    setResult(null)
    try {
      const res = await applySoulTemplate(selected.metadata.name, 'war-room-operator')
      setResult({
        type: 'success',
        message: `Template "${selected.metadata.display_name}" applied as proposal ${res.proposal_id} (${res.diff_summary.length} change${res.diff_summary.length !== 1 ? 's' : ''}). Go to Pending Proposals to approve and activate.`,
      })
      onApplied()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to apply template'
      setResult({ type: 'error', message: msg })
    } finally {
      setApplying(false)
    }
  }

  const industries = Array.from(new Set(templates.map(t => t.industry)))

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      <div className="flex items-center gap-3">
        <span className="text-[10px] text-text-muted uppercase tracking-wider">Industry</span>
        <div className="flex gap-1">
          <button
            onClick={() => setIndustryFilter('')}
            className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
              !industryFilter
                ? 'bg-accent-primary text-white'
                : 'bg-surface-input text-text-muted hover:text-text-primary'
            }`}
          >
            All
          </button>
          {industries.map(ind => (
            <button
              key={ind}
              onClick={() => setIndustryFilter(ind)}
              className={`px-2 py-0.5 text-[10px] rounded capitalize transition-colors ${
                industryFilter === ind
                  ? 'bg-accent-primary text-white'
                  : 'bg-surface-input text-text-muted hover:text-text-primary'
              }`}
            >
              {ind}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-text-muted">Loading templates...</p>
      ) : templates.length === 0 ? (
        <p className="text-sm text-text-muted">No templates available.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {templates.map(t => (
            <button
              key={t.name}
              onClick={() => handleSelect(t.name)}
              className={`text-left p-3 rounded-lg border transition-colors ${
                selected?.metadata.name === t.name
                  ? 'border-accent-primary bg-accent-primary/5'
                  : 'border-border-default bg-surface-card-elevated hover:border-accent-primary/50'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-semibold text-text-primary">{t.display_name}</span>
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-primary/10 text-accent-primary capitalize">
                  {t.industry}
                </span>
              </div>
              <p className="text-[11px] text-text-secondary leading-relaxed line-clamp-2">
                {t.description}
              </p>
              {t.tags.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {t.tags.slice(0, 4).map(tag => (
                    <span key={tag} className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-input text-text-muted">
                      {tag}
                    </span>
                  ))}
                  {t.tags.length > 4 && (
                    <span className="text-[9px] text-text-muted">+{t.tags.length - 4}</span>
                  )}
                </div>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Template detail / diff view */}
      {detailLoading && (
        <p className="text-sm text-text-muted">Loading template details...</p>
      )}

      {selected && !detailLoading && (
        <div className="border border-border-default rounded-lg overflow-hidden">
          <div className="p-3 bg-surface-card-elevated border-b border-border-default/50">
            <div className="flex items-center justify-between">
              <div>
                <h4 className="text-xs font-semibold text-text-primary">{selected.metadata.display_name}</h4>
                <p className="text-[10px] text-text-muted mt-0.5">
                  v{selected.metadata.version} by {selected.metadata.author}
                </p>
              </div>
              <button
                onClick={handleApply}
                disabled={applying}
                className="px-3 py-1.5 text-[11px] font-medium rounded bg-accent-primary text-white hover:bg-accent-primary/80 transition-colors disabled:opacity-50"
              >
                {applying ? 'Applying...' : 'Apply as Proposal'}
              </button>
            </div>
          </div>
          <div className="p-3 bg-surface-card">
            <p className="text-[10px] text-text-muted uppercase tracking-wider mb-2">Template Soul YAML</p>
            <pre className="text-[11px] font-mono text-text-primary leading-relaxed bg-surface-input rounded p-3 overflow-auto max-h-[400px] whitespace-pre-wrap">
              {selected.raw_yaml}
            </pre>
          </div>
        </div>
      )}

      {result && (
        <div className={`p-2 rounded border text-[11px] leading-relaxed ${
          result.type === 'success'
            ? 'bg-state-healthy/10 border-state-healthy/30 text-state-healthy'
            : 'bg-state-error/10 border-state-error/30 text-state-error'
        }`}>
          {result.message}
        </div>
      )}

      <p className="text-[10px] text-text-muted">
        Applying a template creates a Soul Amendment Proposal. Review the diff, then approve and activate via the Pending Proposals section.
      </p>
    </div>
  )
}

// ── Main Soul Inspector Page ────────────────────────────────────────
export function SoulInspector() {
  usePageTitle('Soul Inspector')
  const { data: statusData, refetch: refetchStatus } = usePolling({ fetcher: fetchSoulStatus, interval: 30000 })
  const { data: crusaderStatus } = usePolling<CrusaderStatusResponse>({ fetcher: fetchCrusaderStatus, interval: 5000 })
  const [content, setContent] = useState<SoulContentResponse | null>(null)
  const [contentLoading, setContentLoading] = useState(true)
  const [tab, setTab] = useState<'view' | 'edit' | 'templates'>('view')
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [confirmAction, setConfirmAction] = useState<SoulConfirmAction | null>(null)
  const [actionResult, setActionResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const crusaderActive = crusaderStatus?.crusader_mode ?? false

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
    const loadingKey = confirmAction.type === 'activate-version' ? confirmAction.version : confirmAction.id
    setActionLoading(loadingKey)
    setActionResult(null)
    try {
      if (confirmAction.type === 'approve') {
        await approveSoulProposal(confirmAction.id)
        setActionResult({ type: 'success', message: `Proposal ${confirmAction.id} approved. You can now activate it.` })
      } else if (confirmAction.type === 'activate') {
        const res = await activateSoulProposal(confirmAction.id)
        setActionResult({ type: 'success', message: `Soul activated: ${res.active_version ?? 'new version'}` })
        loadContent() // refresh the viewer
      } else {
        const res = await activateSoulVersion(confirmAction.version)
        setActionResult({ type: 'success', message: `Soul activated: ${res.active_version ?? confirmAction.version}` })
        loadContent()
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
  const activeWorkflow = SOUL_WORKFLOWS.find(workflow => workflow.id === tab) ?? DEFAULT_SOUL_WORKFLOW
  const approvedProposalCount = proposals.filter(proposal => proposal.status === 'approved').length
  const pendingProposalCount = proposals.filter(proposal => proposal.status === 'pending').length

  return (
    <div>
      <section className="mb-6 overflow-hidden rounded-lg border border-border-default bg-surface-card">
        <div className="flex flex-col gap-4 p-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent-primary">Governance</p>
            <h2 className="mt-1 text-xl font-semibold text-text-primary">Soul Inspector</h2>
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">
              Inspect the active constitution, test behavior gates, create amendment proposals, and apply governed templates.
            </p>
          </div>
          {statusData && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:min-w-[34rem]">
              <div className="rounded-md border border-border-default bg-surface-card-elevated px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Active Version</p>
                <p className="mt-1 truncate font-mono text-sm font-semibold text-text-primary">{statusData.active_version}</p>
              </div>
              <div className="rounded-md border border-border-default bg-surface-card-elevated px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Source</p>
                <p className="mt-1 truncate text-xs text-text-secondary">{soulSourceLabel(statusData.active_source)}</p>
              </div>
              <div className="rounded-md border border-border-default bg-surface-card-elevated px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Open Proposals</p>
                <p className="mt-1 font-mono text-sm font-semibold text-text-primary">{proposals.length}</p>
              </div>
              <div className="rounded-md border border-border-default bg-surface-card-elevated px-3 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Versions</p>
                <p className="mt-1 font-mono text-sm font-semibold text-text-primary">{statusData.available_versions.length}</p>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Crusader Mode Banner */}
      {crusaderActive && (
        <div className="mb-6 p-3 bg-accent-secondary/10 border border-accent-secondary/30 rounded-lg">
          <div className="flex items-center gap-2 mb-1">
            <span className="w-2 h-2 rounded-full bg-accent-secondary animate-pulse" />
            <span className="text-sm font-semibold text-accent-secondary">Crusader Soul Override Active</span>
          </div>
          <p className="text-xs text-text-secondary">
            The Crusader constitution is temporarily active. This is a session-scoped override with expanded autonomy and reduced approval gates.
            {crusaderStatus?.soul_override && (
              <> Original soul version <span className="font-mono font-semibold">{crusaderStatus.soul_override}</span> will be restored when Crusader Mode is deactivated.</>
            )}
          </p>
        </div>
      )}

      {/* Active Overlays Banner */}
      {(statusData?.active_overlays ?? []).length > 0 && (
        <div className="mb-6 space-y-2">
          {(statusData?.active_overlays ?? []).map((overlay: SoulOverlayInfo) => (
            <div key={overlay.name} className="p-3 bg-accent-primary/8 border border-accent-primary/20 rounded-lg">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="w-2 h-2 rounded-full bg-accent-primary" />
                <span className="text-sm font-semibold text-accent-primary">
                  Soul Overlay: {overlay.name.toUpperCase()}
                </span>
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-primary/15 text-accent-primary font-mono">
                  {overlay.feature_flag}
                </span>
              </div>
              <p className="text-xs text-text-secondary mb-2">{overlay.description}</p>
              <div className="flex flex-wrap gap-3 text-[10px] text-text-muted">
                <span>+{overlay.risk_rules_count} risk rules</span>
                <span>+{overlay.tone_invariants_count} tone invariants</span>
                <span>+{overlay.memory_ethics_count} memory ethics</span>
                <span>+{overlay.autonomy_additions} autonomy entries</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {contentLoading && !content ? (
        <p className="text-text-muted text-sm">Loading soul data...</p>
      ) : !content ? (
        <div className="bg-surface-card border border-border-default rounded-lg p-6 text-center">
          <p className="text-text-muted">Soul system not initialized. Enable FEATURE_SOUL in Kill Switches.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {/* View / Edit tabs */}
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            {SOUL_WORKFLOWS.map(workflow => (
              <button
                key={workflow.id}
                onClick={() => setTab(workflow.id)}
                className={`rounded-lg border p-3 text-left transition-colors ${
                  tab === workflow.id
                    ? 'border-accent-primary bg-accent-primary/10'
                    : 'border-border-default bg-surface-card hover:border-accent-primary/50'
                }`}
              >
                <p className={`text-[10px] font-semibold uppercase tracking-wider ${
                  tab === workflow.id ? 'text-accent-primary' : 'text-text-muted'
                }`}>
                  {workflow.kicker}
                </p>
                <h3 className="mt-1 text-sm font-semibold text-text-primary">{workflow.label}</h3>
                <p className="mt-1 text-xs leading-relaxed text-text-muted">{workflow.description}</p>
              </button>
            ))}
          </div>

          {/* Tab content */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <div className="mb-4 flex flex-col gap-1 border-b border-border-default pb-3 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">{activeWorkflow.kicker}</p>
                <h3 className="text-sm font-semibold text-text-primary">{activeWorkflow.label}</h3>
              </div>
              <p className="text-xs text-text-muted">{activeWorkflow.description}</p>
            </div>
            {tab === 'view' ? (
              <SoulViewer
                soul={content.soul}
                rawYaml={content.raw_yaml}
                disabled={crusaderActive}
                onProposed={() => { refetchStatus(); loadContent() }}
              />
            ) : tab === 'templates' ? (
              <TemplateBrowser onApplied={() => { refetchStatus(); loadContent() }} />
            ) : crusaderActive ? (
              <div className="p-4 text-center">
                <p className="text-sm text-text-muted">Soul editing is disabled while Crusader Mode is active.</p>
                <p className="text-xs text-text-muted mt-1">Deactivate Crusader Mode to edit the soul constitution.</p>
              </div>
            ) : (
              <SoulEditor
                rawYaml={content.raw_yaml}
                onProposed={() => { refetchStatus(); loadContent() }}
              />
            )}
          </section>

          {/* Pending Proposals */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Change Control</p>
                <h3 className="text-sm font-semibold text-text-primary">Amendment Proposals</h3>
              </div>
              <div className="flex flex-wrap gap-2 text-[10px] text-text-muted">
                <span className="rounded border border-border-default bg-surface-input px-2 py-1">{pendingProposalCount} pending</span>
                <span className="rounded border border-border-default bg-surface-input px-2 py-1">{approvedProposalCount} approved</span>
              </div>
            </div>

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
                        <p className="text-[10px] text-text-muted mt-1">by {p.author} {p.created_at ? `at ${formatTimestamp(p.created_at)}` : ''}</p>
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
              <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Rollback Surface</p>
                  <h3 className="text-sm font-semibold text-text-primary">Available Versions</h3>
                </div>
                <span className="w-fit rounded border border-border-default bg-surface-input px-2 py-1 text-[10px] text-text-muted">
                  {statusData.available_versions.length} retained
                </span>
              </div>
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {statusData.available_versions.map(v => (
                  <div
                    key={v}
                    className={`flex items-center justify-between gap-3 px-3 py-2 rounded border text-xs ${
                      v === statusData.active_version
                        ? 'bg-accent-primary/10 border-accent-primary/30 text-accent-primary'
                        : 'bg-surface-card-elevated border-border-default text-text-secondary'
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-semibold">{v}</span>
                        {v === statusData.active_version && (
                          <span className="text-[9px] uppercase tracking-wider">active</span>
                        )}
                      </div>
                      <p className="truncate text-[10px] text-text-muted">
                        {soulSourceLabel(statusData.version_sources?.[v])}
                      </p>
                    </div>
                    {v !== statusData.active_version && (
                      <button
                        type="button"
                        onClick={() => setConfirmAction({ type: 'activate-version', version: v })}
                        disabled={crusaderActive || actionLoading === v}
                        className="shrink-0 px-2 py-1 text-[10px] font-medium rounded bg-accent-primary/15 text-accent-primary hover:bg-accent-primary/25 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      >
                        Activate
                      </button>
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
            : confirmAction?.type === 'activate-version'
              ? `Activate existing soul version ${confirmAction.version}? This will switch the active soul and the linter will validate the target version before activation.`
              : `Activate proposal ${confirmAction?.id ?? ''}? This will change the active soul version. The soul linter will validate the change before activation.`
        }
        variant={confirmAction?.type === 'activate' || confirmAction?.type === 'activate-version' ? 'destructive' : 'default'}
        confirmLabel={confirmAction?.type === 'approve' ? 'Approve' : 'Activate'}
        onConfirm={handleProposalAction}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  )
}
