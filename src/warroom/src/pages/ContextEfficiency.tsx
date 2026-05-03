import { useState } from 'react'
import { Link } from 'react-router-dom'
import { compileContext } from '@/api'
import { MetricCard } from '@/components'
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
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Context Efficiency</h2>
          <p className="text-sm text-text-muted mt-2">
            Inspect how Lancelot budgets core context, dynamic memory, retrieval misses, and reusable task evidence.
          </p>
        </div>
        <Link
          to="/memory"
          className="inline-flex items-center px-4 py-2 text-sm rounded-md border border-border-default text-text-primary hover:border-border-active"
        >
          Memory
        </Link>
      </div>

      <section className="bg-surface-card border border-border-default rounded-lg p-4">
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
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={handleCompile}
            disabled={busy}
            className="px-4 py-2 bg-accent-primary text-white text-sm rounded-md hover:bg-accent-primary/80 disabled:opacity-60"
          >
            {busy ? 'Compiling...' : 'Run Diagnostic'}
          </button>
          {result ? <span className="text-xs font-mono text-text-muted">context {result.context_id}</span> : null}
        </div>
        {error ? (
          <div className="mt-4 rounded-lg border border-state-error/40 bg-state-error/10 px-4 py-3 text-sm text-state-error">
            {error}
          </div>
        ) : null}
      </section>

      {result ? (
        <>
          <div className="grid grid-cols-2 xl:grid-cols-5 gap-4">
            <MetricCard label="Context Tokens" value={numberMetric(efficiency.total_context_tokens)} />
            <MetricCard label="Budget Used" value={percentMetric(efficiency.budget_used_ratio)} />
            <MetricCard label="Static Tokens" value={numberMetric(efficiency.static_context_tokens)} />
            <MetricCard label="Dynamic Tokens" value={numberMetric(efficiency.dynamic_context_tokens)} />
            <MetricCard label="Retrieval Miss" value={percentMetric(efficiency.retrieval_miss_rate)} />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            <section className="bg-surface-card border border-border-default rounded-lg p-4">
              <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
                Memory Hits
              </h3>
              <div className="space-y-3 text-sm">
                <div className="flex justify-between gap-3">
                  <span className="text-text-muted">Considered</span>
                  <span className="font-mono text-text-primary">{numberMetric(efficiency.memory_hits_considered)}</span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-text-muted">Included</span>
                  <span className="font-mono text-state-healthy">{numberMetric(efficiency.memory_hits_included)}</span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-text-muted">Excluded</span>
                  <span className="font-mono text-state-warning">{numberMetric(efficiency.memory_hits_excluded)}</span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-text-muted">Working included</span>
                  <span className="font-mono text-text-primary">{numberMetric(efficiency.working_memory_hits_included)}</span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-text-muted">Retrieval included</span>
                  <span className="font-mono text-text-primary">{numberMetric(efficiency.retrieval_hits_included)}</span>
                </div>
              </div>
            </section>

            <section className="bg-surface-card border border-border-default rounded-lg p-4">
              <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
                Token Breakdown
              </h3>
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
              <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
                Reuse And Cache
              </h3>
              <div className="space-y-3 text-sm">
                <div className="flex justify-between gap-3">
                  <span className="text-text-muted">Cache eligible</span>
                  <span className="font-mono text-text-primary">{cacheEligibility?.eligible ? 'yes' : 'no'}</span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-text-muted">Template hits</span>
                  <span className="font-mono text-text-primary">{numberMetric(templateReuse?.template_hits)}</span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-text-muted">Template hit rate</span>
                  <span className="font-mono text-text-primary">{percentMetric(templateReuse?.template_hit_rate)}</span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-text-muted">Compaction savings</span>
                  <span className="font-mono text-text-primary">{numberMetric(efficiency.compaction_savings_tokens)} tokens</span>
                </div>
              </div>
            </section>
          </div>

          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Exclusions
            </h3>
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
          <p className="text-sm text-text-muted">
            Run a diagnostic to produce the same context efficiency telemetry that is written into compile receipts.
          </p>
        </section>
      )}
    </div>
  )
}
