import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, Database, Gauge, MemoryStick, Search, Zap } from 'lucide-react'
import { compileContext } from '@/api'
import { usePageTitle } from '@/hooks'
import type { CompileContextResponse } from '@/types/api'

function numberMetric(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function percentMetric(value: unknown): string {
  const ratio = numberMetric(value)
  return `${Math.round(ratio * 100)}%`
}

function formatSectionName(name: string): string {
  return name.replace(/_/g, ' ')
}

function renderReasons(reasons: unknown): Array<[string, number]> {
  if (!reasons || typeof reasons !== 'object' || Array.isArray(reasons)) return []
  return Object.entries(reasons as Record<string, unknown>)
    .map(([key, value]) => [key, numberMetric(value)] as [string, number])
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1])
}

type EfficiencyTileTone = 'accent' | 'healthy' | 'warning' | 'muted'

const efficiencyTileToneClass: Record<EfficiencyTileTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function EfficiencyTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: EfficiencyTileTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${efficiencyTileToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

function FieldRow({ label, value, tone = 'text-text-primary' }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-text-muted">{label}</span>
      <span className={`font-mono ${tone}`}>{value}</span>
    </div>
  )
}

export function ContextEfficiency() {
  usePageTitle('Context Efficiency')

  const [objective, setObjective] = useState('')
  const [questId, setQuestId] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [result, setResult] = useState<CompileContextResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const efficiency = result?.context_efficiency ?? {}
  const tokenBreakdown = result?.token_breakdown ?? {}
  const sectionPercentages =
    efficiency.section_percentages && typeof efficiency.section_percentages === 'object' && !Array.isArray(efficiency.section_percentages)
      ? efficiency.section_percentages as Record<string, unknown>
      : {}
  const exclusionReasons = renderReasons(efficiency.exclusion_reasons)
  const cacheEligibility = efficiency.cache_eligibility as Record<string, unknown> | undefined
  const templateReuse = efficiency.template_reuse as Record<string, unknown> | undefined

  const handleCompile = async () => {
    if (!objective.trim()) {
      setError('Enter an objective before running a context diagnostic.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const response = await compileContext({
        objective: objective.trim(),
        quest_id: questId.trim() || undefined,
        search_query: searchQuery.trim() || undefined,
      })
      setResult(response)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border-default bg-surface-card p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-accent-primary">
              Memory Diagnostics
            </div>
            <h2 className="mt-2 text-2xl font-semibold text-text-primary">Context Efficiency</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Compile a task preview to inspect token budget pressure, memory inclusion, retrieval misses, and reusable
              task evidence before the session grows.
            </p>
          </div>
          <Link
            to="/memory"
            className="inline-flex w-fit items-center gap-2 px-4 py-2 text-sm rounded-md border border-border-default text-text-primary hover:border-border-active"
          >
            <MemoryStick className="h-4 w-4" aria-hidden="true" />
            Memory Overview
          </Link>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <EfficiencyTile
            label="Diagnostic State"
            value={result ? 'Ready' : 'Idle'}
            detail={result ? `Context ${result.context_id}` : 'Run a diagnostic to populate telemetry.'}
            tone={result ? 'healthy' : 'muted'}
          />
          <EfficiencyTile
            label="Budget Used"
            value={result ? percentMetric(efficiency.budget_used_ratio) : '--'}
            detail="Share of available context budget consumed."
            tone={numberMetric(efficiency.budget_used_ratio) > 0.8 ? 'warning' : 'accent'}
          />
          <EfficiencyTile
            label="Retrieval Miss"
            value={result ? percentMetric(efficiency.retrieval_miss_rate) : '--'}
            detail="Candidate retrieval misses in this compile."
            tone={numberMetric(efficiency.retrieval_miss_rate) > 0.2 ? 'warning' : 'healthy'}
          />
          <EfficiencyTile
            label="Compaction"
            value={result ? numberMetric(efficiency.compaction_savings_tokens).toLocaleString() : '--'}
            detail="Estimated tokens saved by compaction."
          />
        </div>
      </div>

      <section className="bg-surface-card border border-border-default rounded-lg p-4">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Gauge className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Diagnostic Input</h3>
            </div>
            <p className="mt-1 text-xs text-text-muted">
              Objective is required. Quest and search query narrow the context preview when available.
            </p>
          </div>
          {result ? (
            <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
              {result.context_id}
            </span>
          ) : null}
        </div>
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_220px] gap-4">
          <label className="block">
            <span className="block text-xs font-medium uppercase tracking-wider text-text-secondary mb-2">
              Objective
            </span>
            <textarea
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              rows={3}
              placeholder="Describe the enterprise task or long-running session you want to inspect..."
              className="w-full bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-active resize-y"
            />
          </label>
          <div className="space-y-3">
            <label className="block">
              <span className="block text-xs font-medium uppercase tracking-wider text-text-secondary mb-2">
                Quest
              </span>
              <input
                value={questId}
                onChange={(event) => setQuestId(event.target.value)}
                placeholder="optional"
                className="w-full bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-active"
              />
            </label>
            <label className="block">
              <span className="block text-xs font-medium uppercase tracking-wider text-text-secondary mb-2">
                Search Query
              </span>
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="defaults to objective"
                className="w-full bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-active"
              />
            </label>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            onClick={handleCompile}
            disabled={busy}
            className="inline-flex items-center gap-2 px-4 py-2 bg-accent-primary text-white text-sm rounded-md hover:bg-accent-primary/80 disabled:opacity-60"
          >
            <Zap className="h-4 w-4" aria-hidden="true" />
            {busy ? 'Compiling...' : 'Run Diagnostic'}
          </button>
          <span className="text-xs text-text-muted">
            Results are a preview of the context efficiency telemetry written into compile receipts.
          </span>
        </div>
        {error ? (
          <div className="mt-4 rounded-lg border border-state-error/40 bg-state-error/10 px-4 py-3 text-sm text-state-error">
            {error}
          </div>
        ) : null}
      </section>

      {result ? (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <EfficiencyTile
              label="Context Tokens"
              value={numberMetric(efficiency.total_context_tokens).toLocaleString()}
              detail="Total compiled context."
              tone="accent"
            />
            <EfficiencyTile
              label="Static Tokens"
              value={numberMetric(efficiency.static_context_tokens).toLocaleString()}
              detail="Core and system material."
            />
            <EfficiencyTile
              label="Dynamic Tokens"
              value={numberMetric(efficiency.dynamic_context_tokens).toLocaleString()}
              detail="Memory and retrieval content."
            />
            <EfficiencyTile
              label="Included Hits"
              value={numberMetric(efficiency.memory_hits_included).toLocaleString()}
              detail="Memory hits admitted."
              tone="healthy"
            />
            <EfficiencyTile
              label="Excluded Hits"
              value={numberMetric(efficiency.memory_hits_excluded).toLocaleString()}
              detail="Memory hits held out."
              tone={numberMetric(efficiency.memory_hits_excluded) > 0 ? 'warning' : 'muted'}
            />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            <section className="bg-surface-card border border-border-default rounded-lg p-4">
              <div className="mb-3 flex items-center gap-2">
                <Database className="h-4 w-4 text-state-healthy" aria-hidden="true" />
                <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                  Memory Hits
                </h3>
              </div>
              <div className="space-y-3 text-sm">
                <FieldRow label="Considered" value={numberMetric(efficiency.memory_hits_considered)} />
                <FieldRow label="Included" value={numberMetric(efficiency.memory_hits_included)} tone="text-state-healthy" />
                <FieldRow label="Excluded" value={numberMetric(efficiency.memory_hits_excluded)} tone="text-state-warning" />
                <FieldRow label="Working included" value={numberMetric(efficiency.working_memory_hits_included)} />
                <FieldRow label="Retrieval included" value={numberMetric(efficiency.retrieval_hits_included)} />
              </div>
            </section>

            <section className="bg-surface-card border border-border-default rounded-lg p-4">
              <div className="mb-3 flex items-center gap-2">
                <Activity className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                  Token Breakdown
                </h3>
              </div>
              <div className="space-y-3">
                {Object.entries(tokenBreakdown).map(([section, tokens]) => {
                  const percent = numberMetric(sectionPercentages[section]) * 100
                  return (
                    <div key={section}>
                      <div className="flex justify-between gap-3 text-xs">
                        <span className="text-text-secondary capitalize">{formatSectionName(section)}</span>
                        <span className="font-mono text-text-muted">{tokens.toLocaleString()} tokens</span>
                      </div>
                      <div className="mt-1 h-1 rounded-full bg-surface-input overflow-hidden">
                        <div
                          className="h-full rounded-full bg-accent-primary"
                          style={{ width: `${Math.min(100, percent)}%` }}
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>

            <section className="bg-surface-card border border-border-default rounded-lg p-4">
              <div className="mb-3 flex items-center gap-2">
                <Zap className="h-4 w-4 text-state-warning" aria-hidden="true" />
                <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                  Reuse And Cache
                </h3>
              </div>
              <div className="space-y-3 text-sm">
                <FieldRow label="Cache eligible" value={cacheEligibility?.eligible ? 'yes' : 'no'} />
                <FieldRow label="Template hits" value={numberMetric(templateReuse?.template_hits)} />
                <FieldRow label="Template hit rate" value={percentMetric(templateReuse?.template_hit_rate)} />
                <FieldRow
                  label="Compaction savings"
                  value={`${numberMetric(efficiency.compaction_savings_tokens).toLocaleString()} tokens`}
                />
              </div>
            </section>
          </div>

          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <div className="mb-3 flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <Search className="h-4 w-4 text-state-warning" aria-hidden="true" />
                  <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                    Exclusions
                  </h3>
                </div>
                <p className="mt-1 text-xs text-text-muted">Why candidate memory was held out of this compile.</p>
              </div>
              <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
                {exclusionReasons.length}
              </span>
            </div>
            {exclusionReasons.length === 0 ? (
              <p className="text-sm text-text-muted">No candidate exclusions were recorded for this compile.</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                {exclusionReasons.map(([reason, count]) => (
                  <div key={reason} className="rounded-md border border-border-default bg-surface-card-elevated p-3">
                    <p className="text-xs text-text-secondary capitalize">{formatSectionName(reason)}</p>
                    <p className="mt-1 text-xl font-mono text-text-primary">{count}</p>
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      ) : (
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <div className="flex items-start gap-3">
            <Gauge className="mt-0.5 h-4 w-4 text-accent-primary" aria-hidden="true" />
            <div>
              <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">No Diagnostic Yet</h3>
              <p className="mt-1 text-sm text-text-muted">
                Run a diagnostic to produce the same context efficiency telemetry that is written into compile receipts.
              </p>
            </div>
          </div>
        </section>
      )}
    </div>
  )
}
