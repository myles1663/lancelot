import { useState } from 'react'
import { usePolling } from '@/hooks'
import { fetchSystemStatus, fetchFlags, toggleFlag } from '@/api'
import { StatusDot, ConfirmDialog } from '@/components'
import type { FlagInfo } from '@/api/flags'

export function KillSwitches() {
  const { data } = usePolling({ fetcher: fetchSystemStatus, interval: 10000 })
  const { data: flagsData, refetch: refetchFlags } = usePolling({ fetcher: fetchFlags, interval: 10000 })
  const [pendingToggle, setPendingToggle] = useState<string | null>(null)
  const [restartBanner, setRestartBanner] = useState<string | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)

  const flags = flagsData?.flags ?? {}

  const handleToggle = async (name: string, info: FlagInfo) => {
    if (info.restart_required) {
      setPendingToggle(name)
      return
    }
    await doToggle(name)
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

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Kill Switches</h2>

      {restartBanner && (
        <div className="mb-4 p-3 bg-state-degraded/10 border border-state-degraded/30 rounded-lg flex items-center justify-between">
          <span className="text-sm text-state-degraded">{restartBanner}</span>
          <button
            onClick={() => setRestartBanner(null)}
            className="text-xs text-text-muted hover:text-text-primary ml-4"
          >
            Dismiss
          </button>
        </div>
      )}

      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          System State
        </h3>
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

      <section className="bg-surface-card border border-border-default rounded-lg p-4">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          Feature Flags
        </h3>
        {Object.keys(flags).length === 0 ? (
          <p className="text-sm text-text-muted">Loading flags...</p>
        ) : (
          <div className="space-y-2">
            {Object.entries(flags).map(([flag, info]) => (
              <div key={flag} className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
                <div className="flex items-center gap-3 min-w-0">
                  <span className="text-xs font-mono text-text-primary truncate">{flag}</span>
                  {info.restart_required && (
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-state-degraded/15 text-state-degraded whitespace-nowrap">
                      restart
                    </span>
                  )}
                </div>
                <button
                  onClick={() => handleToggle(flag, info)}
                  disabled={toggling === flag}
                  className={`relative w-11 h-6 rounded-full transition-colors duration-200 flex-shrink-0 ml-3 ${
                    info.enabled
                      ? 'bg-state-healthy'
                      : 'bg-surface-input border border-border-default'
                  } ${toggling === flag ? 'opacity-50' : 'cursor-pointer'}`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform duration-200 ${
                      info.enabled ? 'translate-x-5' : 'translate-x-0'
                    }`}
                  />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

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
