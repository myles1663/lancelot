import { useState } from 'react'
import { usePolling } from '@/hooks'
import { sendMessage, fetchCrusaderStatus, activateCrusader, deactivateCrusader } from '@/api'
import { ConfirmDialog } from '@/components'
import type { CrusaderStatusResponse, CrusaderActionResponse } from '@/types/api'

type ConfirmAction = 'crusader_on' | 'crusader_off' | 'pause' | 'emergency_stop' | null

const ACTION_DESCRIPTIONS: Record<NonNullable<ConfirmAction>, { title: string; description: string; variant: 'default' | 'destructive' }> = {
  crusader_on: {
    title: 'Activate Crusader Mode',
    description:
      'Crusader Mode will elevate capabilities: enable agentic loop, task graph, CLI tools, network access, and connectors. Governance gates will be reduced. Soul will switch to Crusader constitution.',
    variant: 'default',
  },
  crusader_off: {
    title: 'Deactivate Crusader Mode',
    description: 'All feature flags and the soul version will be restored to their previous state.',
    variant: 'default',
  },
  pause: {
    title: 'Pause Agent',
    description: 'Lancelot will stop processing new tasks until resumed. Active tasks will complete.',
    variant: 'default',
  },
  emergency_stop: {
    title: 'Emergency Stop',
    description:
      'Immediately halt all agent activity. Active tasks will be interrupted. Use only in genuine emergencies.',
    variant: 'destructive',
  },
}

export function ControlsPanel() {
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null)
  const [executing, setExecuting] = useState(false)
  const [lastResult, setLastResult] = useState<CrusaderActionResponse | null>(null)

  const { data: crusaderStatus } = usePolling<CrusaderStatusResponse>({
    fetcher: fetchCrusaderStatus,
    interval: 5000,
  })

  const crusaderActive = crusaderStatus?.crusader_mode ?? false

  const handleConfirm = async () => {
    if (!confirmAction) return
    setExecuting(true)
    setLastResult(null)
    try {
      if (confirmAction === 'crusader_on') {
        const res = await activateCrusader()
        setLastResult(res)
      } else if (confirmAction === 'crusader_off') {
        const res = await deactivateCrusader()
        setLastResult(res)
      } else if (confirmAction === 'pause') {
        await sendMessage('PAUSE')
      } else if (confirmAction === 'emergency_stop') {
        await sendMessage('EMERGENCY STOP')
      }
    } finally {
      setExecuting(false)
      setConfirmAction(null)
    }
  }

  return (
    <section className="bg-surface-card border border-border-default rounded-lg p-4">
      <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
        Controls
      </h3>
      <div className="space-y-2">
        <button
          onClick={() => setConfirmAction(crusaderActive ? 'crusader_off' : 'crusader_on')}
          className={`w-full px-3 py-2 text-sm text-left rounded-md border transition-colors ${
            crusaderActive
              ? 'bg-accent-secondary/10 border-accent-secondary/30 text-accent-secondary hover:bg-accent-secondary/20'
              : 'bg-surface-input border-border-default text-text-secondary hover:text-text-primary hover:bg-surface-card-elevated'
          }`}
        >
          Crusader Mode — {crusaderActive ? 'Active' : 'Off'}
        </button>
        <button
          onClick={() => setConfirmAction('pause')}
          className="w-full px-3 py-2 text-sm text-left bg-surface-input border border-border-default rounded-md text-text-secondary hover:text-text-primary hover:bg-surface-card-elevated transition-colors"
        >
          Pause Agent
        </button>
        <button
          onClick={() => setConfirmAction('emergency_stop')}
          className="w-full px-3 py-2 text-sm text-left bg-surface-input border border-state-error/30 rounded-md text-state-error hover:bg-state-error/10 transition-colors"
        >
          Emergency Stop
        </button>
      </div>

      {/* Crusader Mode Status Summary */}
      {crusaderActive && crusaderStatus && (
        <div className="mt-3 p-2.5 bg-accent-secondary/5 border border-accent-secondary/20 rounded-md">
          <p className="text-[11px] font-medium text-accent-secondary mb-1.5">Crusader Mode Active</p>
          <div className="space-y-1 text-[10px] text-text-secondary">
            <p>{crusaderStatus.flag_overrides} flag{crusaderStatus.flag_overrides !== 1 ? 's' : ''} overridden</p>
            {crusaderStatus.soul_override && (
              <p>Soul: crusader (was {crusaderStatus.soul_override})</p>
            )}
            {crusaderStatus.activated_at && (
              <p>Since {new Date(crusaderStatus.activated_at).toLocaleTimeString()}</p>
            )}
          </div>
        </div>
      )}

      {/* Action Result Toast */}
      {lastResult && (
        <div className={`mt-3 p-2.5 rounded-md border text-[11px] ${
          lastResult.status === 'activated' || lastResult.status === 'deactivated'
            ? 'bg-state-healthy/10 border-state-healthy/30 text-state-healthy'
            : 'bg-state-degraded/10 border-state-degraded/30 text-state-degraded'
        }`}>
          <div className="flex items-start justify-between">
            <div>
              <p className="font-medium">{lastResult.status === 'activated' ? 'Crusader Mode Engaged' : lastResult.status === 'deactivated' ? 'Crusader Mode Disengaged' : lastResult.status}</p>
              {lastResult.flag_overrides > 0 && (
                <p className="mt-0.5">{lastResult.flag_overrides} flag{lastResult.flag_overrides !== 1 ? 's' : ''} changed</p>
              )}
              {lastResult.overridden_flags.length > 0 && (
                <p className="mt-0.5 font-mono text-[9px] text-text-muted">
                  {lastResult.overridden_flags.map(f => f.replace('FEATURE_', '')).join(', ')}
                </p>
              )}
            </div>
            <button onClick={() => setLastResult(null)} className="text-text-muted hover:text-text-primary ml-2">&times;</button>
          </div>
        </div>
      )}

      {confirmAction && (
        <ConfirmDialog
          open
          title={ACTION_DESCRIPTIONS[confirmAction].title}
          description={
            executing ? 'Executing...' : ACTION_DESCRIPTIONS[confirmAction].description
          }
          variant={ACTION_DESCRIPTIONS[confirmAction].variant}
          confirmLabel={executing ? 'Executing...' : 'Confirm'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirmAction(null)}
        />
      )}
    </section>
  )
}
