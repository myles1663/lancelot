import { useState } from 'react'
import { usePolling } from '@/hooks'
import { sendMessage } from '@/api'
import { ConfirmDialog } from '@/components'
import type { HealthCheckResponse } from '@/types/api'
import { fetchHealth } from '@/api'

type ConfirmAction = 'crusader_on' | 'crusader_off' | 'pause' | 'emergency_stop' | null

const ACTION_CONFIG: Record<
  NonNullable<ConfirmAction>,
  { title: string; description: string; variant: 'default' | 'destructive'; command: string }
> = {
  crusader_on: {
    title: 'Activate Crusader Mode',
    description:
      'Crusader Mode elevates your authority level, bypassing standard approval gates. Use responsibly.',
    variant: 'default',
    command: 'ENGAGE CRUSADER',
  },
  crusader_off: {
    title: 'Deactivate Crusader Mode',
    description: 'Return to standard operating mode with full governance gates active.',
    variant: 'default',
    command: 'STAND DOWN CRUSADER',
  },
  pause: {
    title: 'Pause Agent',
    description: 'Lancelot will stop processing new tasks until resumed. Active tasks will complete.',
    variant: 'default',
    command: 'PAUSE',
  },
  emergency_stop: {
    title: 'Emergency Stop',
    description:
      'Immediately halt all agent activity. Active tasks will be interrupted. Use only in genuine emergencies.',
    variant: 'destructive',
    command: 'EMERGENCY STOP',
  },
}

export function ControlsPanel() {
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null)
  const [executing, setExecuting] = useState(false)

  const { data: health } = usePolling<HealthCheckResponse>({
    fetcher: fetchHealth,
    interval: 5000,
  })

  const crusaderActive = health?.crusader_mode ?? false

  const handleConfirm = async () => {
    if (!confirmAction) return
    const config = ACTION_CONFIG[confirmAction]
    setExecuting(true)
    try {
      await sendMessage(config.command)
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

      {confirmAction && (
        <ConfirmDialog
          open
          title={ACTION_CONFIG[confirmAction].title}
          description={
            executing ? 'Executing...' : ACTION_CONFIG[confirmAction].description
          }
          variant={ACTION_CONFIG[confirmAction].variant}
          confirmLabel={executing ? 'Executing...' : 'Confirm'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirmAction(null)}
        />
      )}
    </section>
  )
}
