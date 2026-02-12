import { useState } from 'react'
import { usePolling } from '@/hooks'
import { fetchSystemStatus, fetchFlags, toggleFlag } from '@/api'
import { StatusDot, ConfirmDialog } from '@/components'
import type { FlagInfo } from '@/api/flags'

// Category display order
const CATEGORY_ORDER = ['Core Subsystem', 'Tool Fabric', 'Runtime', 'Governance', 'Capabilities', 'Intelligence', 'Other']

export function KillSwitches() {
  const { data } = usePolling({ fetcher: fetchSystemStatus, interval: 10000 })
  const { data: flagsData, refetch: refetchFlags } = usePolling({ fetcher: fetchFlags, interval: 10000 })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [pendingToggle, setPendingToggle] = useState<string | null>(null)
  const [restartBanner, setRestartBanner] = useState<string | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)

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

              return (
                <div key={name} className="bg-surface-card-elevated rounded-md border border-border-default overflow-hidden">
                  {/* Flag row */}
                  <div
                    className="flex items-center justify-between p-3 cursor-pointer hover:bg-surface-input/50 transition-colors"
                    onClick={() => toggleExpand(name)}
                  >
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <span className={`text-[10px] transition-transform ${isExpanded ? 'rotate-90' : ''}`}>&#9654;</span>
                      <span className="text-xs font-mono text-text-primary truncate">{name}</span>
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
                      onClick={(e) => handleToggle(e, name, info)}
                      disabled={toggling === name}
                      className={`relative w-11 h-6 rounded-full transition-colors duration-200 flex-shrink-0 ml-3 ${
                        info.enabled ? 'bg-state-healthy' : 'bg-surface-input border border-border-default'
                      } ${toggling === name ? 'opacity-50' : 'cursor-pointer'}`}
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
    </div>
  )
}
