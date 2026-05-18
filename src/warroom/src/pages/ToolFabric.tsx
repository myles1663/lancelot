import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  FileCheck2,
  PlugZap,
  ServerCog,
  ShieldCheck,
  SlidersHorizontal,
  Wrench,
  XCircle,
} from 'lucide-react'
import { usePolling, usePageTitle } from '@/hooks'
import { fetchHealth, fetchToolsHealth, fetchToolsConfig } from '@/api'
import { StatusDot } from '@/components'

type FabricTileTone = 'accent' | 'healthy' | 'warning' | 'error' | 'muted'

const fabricTileToneClass: Record<FabricTileTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  error: 'border-state-error/30 bg-state-error/10 text-state-error',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function FabricTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: FabricTileTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${fabricTileToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 break-words text-2xl font-semibold leading-tight text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

function statusToDotState(status: string) {
  if (status === 'ok' || status === 'healthy') return 'healthy'
  if (status === 'disabled' || status === 'inactive') return 'inactive'
  if (status === 'degraded') return 'degraded'
  return 'error'
}

function providerTone(state: string): FabricTileTone {
  if (state === 'healthy') return 'healthy'
  if (state === 'degraded') return 'warning'
  return 'error'
}

function postureTone(enabled: boolean, degraded: number, offline: number): FabricTileTone {
  if (!enabled || offline > 0) return 'error'
  if (degraded > 0) return 'warning'
  return 'healthy'
}

function postureLabel(enabled: boolean, degraded: number, offline: number): string {
  if (!enabled) return 'Disabled'
  if (offline > 0) return 'Action Required'
  if (degraded > 0) return 'Degraded'
  return 'Operational'
}

function formatProviderName(name: string): string {
  return name.replace(/_/g, ' ')
}

function formatUptime(seconds?: number): string {
  if (!seconds) return '--'
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.round((seconds % 3600) / 60)
  return `${hours}h ${minutes}m`
}

function ConfigRow({
  label,
  detail,
  state,
  value,
}: {
  label: string
  detail: string
  state: 'healthy' | 'degraded' | 'inactive'
  value: string
}) {
  return (
    <div className="rounded-lg border border-border-default bg-surface-card-elevated px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-text-primary">{label}</div>
          <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
        </div>
        <StatusDot state={state} label={value} />
      </div>
    </div>
  )
}

export function ToolFabric() {
  usePageTitle('Tool Fabric')
  const { data: health, error: healthError } = usePolling({ fetcher: fetchHealth, interval: 10000 })
  const { data: toolsHealth } = usePolling({ fetcher: fetchToolsHealth, interval: 10000 })
  const { data: toolsConfig } = usePolling({ fetcher: fetchToolsConfig, interval: 30000 })

  const systemComponents = health?.components ?? {}
  const providers = toolsHealth?.providers ?? {}
  const providerEntries = Object.entries(providers).sort(([a], [b]) => a.localeCompare(b))
  const summary = toolsHealth?.summary ?? { total_providers: 0, healthy: 0, degraded: 0, offline: 0 }
  const fabricEnabled = toolsHealth?.enabled ?? false
  const fabricPosture = postureLabel(fabricEnabled, summary.degraded, summary.offline)
  const attentionCount = summary.degraded + summary.offline
  const systemProblemCount = Object.values(systemComponents).filter(
    (status) => !['ok', 'disabled'].includes(String(status)),
  ).length

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border-default bg-surface-card px-5 py-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="text-[10px] font-semibold uppercase tracking-widest text-accent-primary">Runtime Tooling</div>
            <h2 className="mt-2 text-2xl font-semibold leading-tight text-text-primary">Tool Fabric</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Monitor provider readiness, fabric safety posture, and the local system dependencies that govern tool execution.
            </p>
          </div>
          <div className={`rounded-lg border px-4 py-3 ${fabricTileToneClass[postureTone(fabricEnabled, summary.degraded, summary.offline)]}`}>
            <div className="flex items-center gap-2">
              {fabricEnabled ? (
                <PlugZap className="h-4 w-4" aria-hidden="true" />
              ) : (
                <XCircle className="h-4 w-4" aria-hidden="true" />
              )}
              <span className="text-sm font-semibold text-text-primary">{fabricPosture}</span>
            </div>
            <div className="mt-2 text-xs leading-5 text-text-muted">
              {fabricEnabled
                ? `${summary.healthy} healthy, ${summary.degraded} degraded, ${summary.offline} offline.`
                : 'Tool execution is not currently available.'}
            </div>
          </div>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <FabricTile
          label="Fabric State"
          value={fabricEnabled ? 'Enabled' : 'Disabled'}
          detail="Top-level tool routing availability."
          tone={fabricEnabled ? 'healthy' : 'error'}
        />
        <FabricTile
          label="Providers"
          value={summary.total_providers}
          detail="Registered execution providers."
          tone="accent"
        />
        <FabricTile
          label="Healthy"
          value={summary.healthy}
          detail="Providers ready for routed work."
          tone="healthy"
        />
        <FabricTile
          label="Needs Attention"
          value={attentionCount}
          detail="Degraded or offline providers."
          tone={attentionCount > 0 ? 'warning' : 'muted'}
        />
        <FabricTile
          label="System Issues"
          value={systemProblemCount}
          detail="Gateway components outside normal state."
          tone={systemProblemCount > 0 ? 'warning' : 'muted'}
        />
      </section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
        <section className="rounded-lg border border-border-default bg-surface-card">
          <div className="flex flex-col gap-3 border-b border-border-default px-4 py-4 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-2">
              <Wrench className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <div>
                <h3 className="text-sm font-semibold text-text-primary">Provider Readiness</h3>
                <p className="text-xs leading-5 text-text-muted">Execution providers registered with Tool Fabric.</p>
              </div>
            </div>
            <span className="rounded border border-border-default bg-surface-card-elevated px-2 py-1 text-xs font-mono text-text-muted">
              {providerEntries.length} providers
            </span>
          </div>

          {providerEntries.length === 0 ? (
            <div className="px-4 py-10 text-center">
              <Cpu className="mx-auto h-6 w-6 text-text-muted" aria-hidden="true" />
              <div className="mt-2 text-sm font-medium text-text-primary">
                {fabricEnabled ? 'No providers registered' : 'Tool Fabric not active'}
              </div>
              <div className="mt-1 text-xs leading-5 text-text-muted">
                Provider rows appear here when the fabric reports available execution backends.
              </div>
            </div>
          ) : (
            <div className="divide-y divide-border-default">
              {providerEntries.map(([pid, prov]) => (
                <div key={pid} className="px-4 py-4">
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div className="min-w-0">
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <span className={`rounded border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${fabricTileToneClass[providerTone(prov.state)]}`}>
                          {prov.state}
                        </span>
                        <span className="min-w-0 break-words font-mono text-sm text-text-primary">{pid}</span>
                      </div>
                      <p className="mt-2 text-xs leading-5 text-text-muted">{formatProviderName(pid)}</p>
                      {prov.error && (
                        <div className="mt-3 rounded border border-state-error/30 bg-state-error/10 px-3 py-2 text-xs leading-5 text-state-error">
                          {prov.error}
                        </div>
                      )}
                    </div>
                    <StatusDot
                      state={prov.state === 'healthy' ? 'healthy' : prov.state === 'degraded' ? 'degraded' : 'error'}
                      label={prov.state}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-4 flex items-center gap-2">
            <SlidersHorizontal className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <div>
              <h3 className="text-sm font-semibold text-text-primary">Fabric Configuration</h3>
              <p className="text-xs leading-5 text-text-muted">Current execution safeguards reported by the backend.</p>
            </div>
          </div>
          <div className="space-y-3">
            <ConfigRow
              label="Tool Fabric"
              detail="Master switch for routed tool access."
              state={toolsConfig?.enabled ? 'healthy' : 'inactive'}
              value={toolsConfig?.enabled ? 'Enabled' : 'Disabled'}
            />
            <ConfigRow
              label="Safe Mode"
              detail="Restricts execution while preserving visibility."
              state={toolsConfig?.safe_mode ? 'degraded' : 'healthy'}
              value={toolsConfig?.safe_mode ? 'Active' : 'Off'}
            />
            <ConfigRow
              label="Receipts"
              detail="Tool execution audit trail availability."
              state={toolsConfig?.receipts ? 'healthy' : 'inactive'}
              value={toolsConfig?.receipts ? 'Enabled' : 'Disabled'}
            />
          </div>
        </section>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <section className="rounded-lg border border-border-default bg-surface-card">
          <div className="flex items-center gap-2 border-b border-border-default px-4 py-4">
            <ServerCog className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <div>
              <h3 className="text-sm font-semibold text-text-primary">System Components</h3>
              <p className="text-xs leading-5 text-text-muted">Gateway-level components that Tool Fabric depends on.</p>
            </div>
          </div>
          {Object.keys(systemComponents).length === 0 ? (
            <div className="px-4 py-8 text-sm text-text-muted">
              {healthError ? `Health data unavailable: ${healthError.message}` : 'Loading component status...'}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2">
              {Object.entries(systemComponents).map(([name, status]) => (
                <div key={name} className="rounded-lg border border-border-default bg-surface-card-elevated px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0 break-words font-mono text-sm text-text-primary">{name}</span>
                    <StatusDot state={statusToDotState(String(status))} label={String(status)} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-4 flex items-center gap-2">
            <Activity className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <div>
              <h3 className="text-sm font-semibold text-text-primary">Runtime Snapshot</h3>
              <p className="text-xs leading-5 text-text-muted">Gateway state that frames provider diagnostics.</p>
            </div>
          </div>
          <div className="space-y-3">
            <div className="rounded-lg border border-border-default bg-surface-card-elevated px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-text-secondary">Version</span>
                <span className="font-mono text-sm text-text-primary">{health?.version ?? __LANCELOT_VERSION__ ?? '--'}</span>
              </div>
            </div>
            <div className="rounded-lg border border-border-default bg-surface-card-elevated px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-text-secondary">Uptime</span>
                <span className="font-mono text-sm text-text-primary">
                  {healthError && !health?.uptime_seconds ? 'Health pending' : formatUptime(health?.uptime_seconds)}
                </span>
              </div>
            </div>
            <div className="rounded-lg border border-border-default bg-surface-card-elevated px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-text-secondary">Crusader Mode</span>
                <StatusDot
                  state={health?.crusader_mode ? 'degraded' : 'healthy'}
                  label={health?.crusader_mode ? 'Active' : 'Off'}
                />
              </div>
            </div>
            <div className="rounded-lg border border-border-default bg-surface-card-elevated px-4 py-3">
              <div className="flex items-start gap-3">
                {toolsConfig?.receipts ? (
                  <FileCheck2 className="mt-0.5 h-4 w-4 text-state-healthy" aria-hidden="true" />
                ) : (
                  <AlertTriangle className="mt-0.5 h-4 w-4 text-state-warning" aria-hidden="true" />
                )}
                <div>
                  <div className="text-sm font-medium text-text-primary">Audit Posture</div>
                  <p className="mt-1 text-xs leading-5 text-text-muted">
                    {toolsConfig?.receipts
                      ? 'Tool calls are expected to leave receipt evidence.'
                      : 'Receipt visibility is not currently reported as enabled.'}
                  </p>
                </div>
              </div>
            </div>
            <div className="rounded-lg border border-border-default bg-surface-card-elevated px-4 py-3">
              <div className="flex items-start gap-3">
                {fabricEnabled && attentionCount === 0 ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 text-state-healthy" aria-hidden="true" />
                ) : (
                  <ShieldCheck className="mt-0.5 h-4 w-4 text-accent-primary" aria-hidden="true" />
                )}
                <div>
                  <div className="text-sm font-medium text-text-primary">Operator Read</div>
                  <p className="mt-1 text-xs leading-5 text-text-muted">
                    {fabricEnabled && attentionCount === 0
                      ? 'Fabric is ready for governed tool routing.'
                      : 'Review provider and configuration panels before depending on routed tool execution.'}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
