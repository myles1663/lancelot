import { useState, useEffect, useCallback } from 'react'
import { usePolling } from '@/hooks'
import { fetchSystemStatus, fetchFlags, toggleFlag, fetchCrusaderStatus } from '@/api'
import { fetchNetworkAllowlist, updateNetworkAllowlist, fetchHostAgentStatus, shutdownHostAgent } from '@/api/flags'
import type { HostAgentStatus } from '@/api/flags'
import { StatusDot, ConfirmDialog } from '@/components'
import type { FlagInfo } from '@/api/flags'
import type { CrusaderStatusResponse } from '@/types/api'

// Category display order
const CATEGORY_ORDER = ['Core Subsystem', 'Tool Fabric', 'Runtime', 'Governance', 'Capabilities', 'Intelligence', 'Other']

// ── Inline Allowlist Editor ─────────────────────────────────────────
function AllowlistEditor() {
  const [domains, setDomains] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fetchNetworkAllowlist()
      setDomains(res.domains)
      setDraft(res.domains.join('\n'))
      setLoaded(true)
    } catch {
      setError('Failed to load allowlist')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      const lines = draft
        .split('\n')
        .map(l => l.trim())
        .filter(l => l && !l.startsWith('#'))
      const res = await updateNetworkAllowlist(lines)
      setDomains(res.domains)
      setDraft(res.domains.join('\n'))
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch {
      setError('Failed to save allowlist')
    } finally {
      setSaving(false)
    }
  }

  const hasChanges = loaded && draft.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#')).sort().join(',') !== [...domains].sort().join(',')

  return (
    <div className="mt-2 p-3 bg-surface-card rounded-lg border border-border-default">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">Allowed Domains</span>
        <span className="text-[10px] text-text-muted">{domains.length} domain{domains.length !== 1 ? 's' : ''}</span>
      </div>
      <textarea
        value={draft}
        onChange={e => setDraft(e.target.value)}
        placeholder="api.github.com&#10;api.anthropic.com&#10;..."
        rows={6}
        className="w-full bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary resize-y"
      />
      <p className="text-[10px] text-text-muted mt-1 mb-2">One domain per line. Exact match only — no wildcards. Lines starting with # are ignored.</p>
      <div className="flex items-center gap-2">
        <button
          onClick={handleSave}
          disabled={saving || !hasChanges}
          className={`px-3 py-1 text-[11px] font-medium rounded transition-colors ${
            hasChanges
              ? 'bg-accent-primary text-white hover:bg-accent-primary/80'
              : 'bg-surface-input text-text-muted cursor-not-allowed'
          }`}
        >
          {saving ? 'Saving...' : 'Save Allowlist'}
        </button>
        {success && <span className="text-[10px] text-state-healthy">Saved</span>}
        {error && <span className="text-[10px] text-state-error">{error}</span>}
      </div>
    </div>
  )
}

// ── Inline Host Agent Panel ─────────────────────────────────────────
function HostAgentPanel() {
  const [status, setStatus] = useState<HostAgentStatus | null>(null)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const poll = useCallback(async () => {
    try {
      const res = await fetchHostAgentStatus()
      setStatus(res)
      setError(null)
    } catch {
      setError('Failed to check agent status')
    }
  }, [])

  useEffect(() => {
    poll()
    const timer = setInterval(poll, 5000)
    return () => clearInterval(timer)
  }, [poll])

  const handleStop = async () => {
    setStopping(true)
    try {
      await shutdownHostAgent()
      setTimeout(poll, 2000)
    } catch {
      setError('Failed to stop agent')
    } finally {
      setStopping(false)
    }
  }

  const reachable = status?.reachable ?? false

  return (
    <div className="mt-2 p-3 bg-surface-card rounded-lg border border-border-default">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">Host Agent</span>
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${reachable ? 'bg-state-healthy animate-pulse' : 'bg-state-error'}`} />
          <span className={`text-[10px] font-medium ${reachable ? 'text-state-healthy' : 'text-state-error'}`}>
            {reachable ? 'Running' : 'Offline'}
          </span>
        </div>
      </div>

      {reachable && status && (
        <div className="space-y-1.5 mb-3">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-text-muted">Platform</span>
            <span className="text-[10px] font-mono text-text-primary">{status.platform} {status.platform_version}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-text-muted">Hostname</span>
            <span className="text-[10px] font-mono text-text-primary">{status.hostname}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-text-muted">Agent Version</span>
            <span className="text-[10px] font-mono text-text-primary">v{status.agent_version}</span>
          </div>
        </div>
      )}

      {reachable ? (
        <button
          onClick={handleStop}
          disabled={stopping}
          className="px-3 py-1 text-[11px] font-medium rounded bg-state-error/15 text-state-error hover:bg-state-error/25 transition-colors disabled:opacity-50"
        >
          {stopping ? 'Stopping...' : 'Stop Agent'}
        </button>
      ) : (
        <div className="space-y-2">
          <p className="text-[10px] text-text-muted leading-relaxed">
            The host agent is not running. To install it as a background service, run:
          </p>
          <code className="block text-[10px] font-mono bg-surface-input rounded px-2 py-1.5 text-text-primary select-all">
            host_agent\install_service.bat
          </code>
          <p className="text-[10px] text-text-muted">
            Or start manually: <code className="font-mono text-text-primary">host_agent\start_agent.bat</code>
          </p>
        </div>
      )}

      {error && <p className="text-[10px] text-state-error mt-2">{error}</p>}
    </div>
  )
}

// ── Main Kill Switches Page ─────────────────────────────────────────
export function KillSwitches() {
  const { data } = usePolling({ fetcher: fetchSystemStatus, interval: 10000 })
  const { data: flagsData, refetch: refetchFlags } = usePolling({ fetcher: fetchFlags, interval: 10000 })
  const { data: crusaderStatus } = usePolling<CrusaderStatusResponse>({ fetcher: fetchCrusaderStatus, interval: 5000 })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [pendingToggle, setPendingToggle] = useState<string | null>(null)
  const [pendingDanger, setPendingDanger] = useState<{ name: string; message: string } | null>(null)
  const [restartBanner, setRestartBanner] = useState<string | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)

  const crusaderActive = crusaderStatus?.crusader_mode ?? false
  const overriddenFlags = new Set(crusaderStatus?.overridden_flags ?? [])

  const flags = flagsData?.flags ?? {}

  // Group flags by category
  const grouped: Record<string, [string, FlagInfo][]> = {}
  for (const [name, info] of Object.entries(flags)) {
    const cat = info.category || 'Other'
    if (!grouped[cat]) grouped[cat] = []
    grouped[cat].push([name, info])
  }

  const sortedCategories = CATEGORY_ORDER.filter(c => grouped[c]?.length)

  const toggleExpand = (name: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const handleToggle = (e: React.MouseEvent, name: string, info: FlagInfo) => {
    e.stopPropagation()
    // Show risk acceptance dialog when enabling a flag with confirm_enable
    if (!info.enabled && info.confirm_enable) {
      setPendingDanger({ name, message: info.confirm_enable })
      return
    }
    if (info.restart_required) {
      setPendingToggle(name)
      return
    }
    doToggle(name)
  }

  const doToggle = async (name: string) => {
    setToggling(name)
    setPendingToggle(null)
    try {
      const res = await toggleFlag(name)
      if (res.restart_required) {
        setRestartBanner(res.message)
      }
      refetchFlags()
    } catch {
      // ignore
    } finally {
      setToggling(null)
    }
  }

  // Check if a flag's dependencies are met
  const depsUnmet = (info: FlagInfo): string[] =>
    info.requires.filter(dep => !flags[dep]?.enabled)

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Kill Switches</h2>

      {/* Crusader Mode Banner */}
      {crusaderActive && (
        <div className="mb-4 p-3 bg-accent-secondary/10 border border-accent-secondary/30 rounded-lg">
          <div className="flex items-center gap-2 mb-1">
            <span className="w-2 h-2 rounded-full bg-accent-secondary animate-pulse" />
            <span className="text-sm font-semibold text-accent-secondary">Crusader Mode Active</span>
          </div>
          <p className="text-xs text-text-secondary">
            {overriddenFlags.size} flag{overriddenFlags.size !== 1 ? 's' : ''} overridden by Crusader Mode. Overridden toggles are locked until Crusader Mode is deactivated.
          </p>
          {overriddenFlags.size > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {Array.from(overriddenFlags).map(f => (
                <span key={f} className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-accent-secondary/15 text-accent-secondary">
                  {f.replace('FEATURE_', '')}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {restartBanner && (
        <div className="mb-4 p-3 bg-state-degraded/10 border border-state-degraded/30 rounded-lg flex items-center justify-between">
          <span className="text-sm text-state-degraded">{restartBanner}</span>
          <button onClick={() => setRestartBanner(null)} className="text-xs text-text-muted hover:text-text-primary ml-4">
            Dismiss
          </button>
        </div>
      )}

      {/* System State */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">System State</h3>
        {!data ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">Onboarding</span>
              <StatusDot state={data.onboarding?.is_ready ? 'healthy' : 'degraded'} label={data.onboarding?.state ?? 'Unknown'} />
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">System Ready</span>
              <StatusDot state={data.onboarding?.is_ready ? 'healthy' : 'inactive'} label={data.onboarding?.is_ready ? 'Ready' : 'Not Ready'} />
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">Cooldown</span>
              <StatusDot
                state={data.cooldown?.active ? 'degraded' : 'healthy'}
                label={data.cooldown?.active ? `Active (${Math.round(data.cooldown.remaining_seconds)}s)` : 'Inactive'}
              />
            </div>
          </div>
        )}
      </section>

      {/* Feature Flags grouped by category */}
      {sortedCategories.map(category => (
        <section key={category} className="bg-surface-card border border-border-default rounded-lg p-4 mb-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">{category}</h3>
          <div className="space-y-1">
            {(grouped[category] ?? []).map(([name, info]) => {
              const isExpanded = expanded.has(name)
              const unmet = depsUnmet(info)
              const hasConflict = info.conflicts.some(c => flags[c]?.enabled)
              const isCrusaderOverride = overriddenFlags.has(name)
              const isToggleDisabled = toggling === name || isCrusaderOverride

              return (
                <div key={name} className={`bg-surface-card-elevated rounded-md border overflow-hidden ${
                  isCrusaderOverride ? 'border-accent-secondary/30' : 'border-border-default'
                }`}>
                  {/* Flag row */}
                  <div
                    className="flex items-center justify-between p-3 cursor-pointer hover:bg-surface-input/50 transition-colors"
                    onClick={() => toggleExpand(name)}
                  >
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <span className={`text-[10px] transition-transform ${isExpanded ? 'rotate-90' : ''}`}>&#9654;</span>
                      <span className="text-xs font-mono text-text-primary truncate">{name}</span>
                      {isCrusaderOverride && (
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-secondary/15 text-accent-secondary whitespace-nowrap">crusader</span>
                      )}
                      {info.restart_required && (
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-primary/15 text-accent-primary whitespace-nowrap">restart</span>
                      )}
                      {unmet.length > 0 && (
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-state-degraded/15 text-state-degraded whitespace-nowrap">deps unmet</span>
                      )}
                      {hasConflict && (
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-state-error/15 text-state-error whitespace-nowrap">conflict</span>
                      )}
                    </div>
                    <button
                      onClick={(e) => !isCrusaderOverride && handleToggle(e, name, info)}
                      disabled={isToggleDisabled}
                      title={isCrusaderOverride ? 'Locked by Crusader Mode' : undefined}
                      className={`relative w-11 h-6 rounded-full transition-colors duration-200 flex-shrink-0 ml-3 ${
                        info.enabled ? 'bg-state-healthy' : 'bg-surface-input border border-border-default'
                      } ${isToggleDisabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                    >
                      <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform duration-200 ${
                        info.enabled ? 'translate-x-5' : 'translate-x-0'
                      }`} />
                    </button>
                  </div>

                  {/* Expanded details */}
                  {isExpanded && (
                    <div className="px-3 pb-3 pt-0 border-t border-border-default/50 space-y-2">
                      {/* Description */}
                      <p className="text-xs text-text-secondary leading-relaxed mt-2">{info.description}</p>

                      {/* Warning */}
                      {info.warning && (
                        <div className="p-2 rounded bg-state-degraded/8 border border-state-degraded/20">
                          <p className="text-[11px] text-state-degraded leading-relaxed">
                            <span className="font-semibold">Warning:</span> {info.warning}
                          </p>
                        </div>
                      )}

                      {/* Dependencies */}
                      {info.requires.length > 0 && (
                        <div className="flex items-start gap-2">
                          <span className="text-[10px] text-text-muted uppercase tracking-wider pt-0.5 whitespace-nowrap">Requires:</span>
                          <div className="flex flex-wrap gap-1">
                            {info.requires.map(dep => {
                              const met = flags[dep]?.enabled
                              return (
                                <span key={dep} className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                                  met ? 'bg-state-healthy/15 text-state-healthy' : 'bg-state-error/15 text-state-error'
                                }`}>
                                  {dep.replace('FEATURE_', '')} {met ? '\u2713' : '\u2717'}
                                </span>
                              )
                            })}
                          </div>
                        </div>
                      )}

                      {/* Conflicts */}
                      {info.conflicts.length > 0 && (
                        <div className="flex items-start gap-2">
                          <span className="text-[10px] text-text-muted uppercase tracking-wider pt-0.5 whitespace-nowrap">Conflicts:</span>
                          <div className="flex flex-wrap gap-1">
                            {info.conflicts.map(c => {
                              const active = flags[c]?.enabled
                              return (
                                <span key={c} className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                                  active ? 'bg-state-error/15 text-state-error' : 'bg-surface-input text-text-muted'
                                }`}>
                                  {c.replace('FEATURE_', '')} {active ? '(active!)' : '(off)'}
                                </span>
                              )
                            })}
                          </div>
                        </div>
                      )}

                      {/* Inline editor for flags with has_editor */}
                      {info.has_editor === 'network_allowlist' && <AllowlistEditor />}
                      {info.has_editor === 'host_agent' && <HostAgentPanel />}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      ))}

      <ConfirmDialog
        open={pendingToggle !== null}
        title="Toggle Startup Flag"
        description={`${pendingToggle ?? ''} controls a startup subsystem. Toggling it will change the in-memory flag immediately, but a container restart is required for the subsystem to fully initialize or shut down. Continue?`}
        variant="destructive"
        confirmLabel="Toggle"
        onConfirm={() => pendingToggle && doToggle(pendingToggle)}
        onCancel={() => setPendingToggle(null)}
      />

      <ConfirmDialog
        open={pendingDanger !== null}
        title="Accept Risk"
        description={pendingDanger?.message ?? ''}
        variant="destructive"
        confirmLabel="I Accept the Risk"
        onConfirm={() => { if (pendingDanger) { doToggle(pendingDanger.name); setPendingDanger(null) } }}
        onCancel={() => setPendingDanger(null)}
      />
    </div>
  )
}
