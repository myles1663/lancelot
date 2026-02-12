import { useState } from 'react'
import { usePolling } from '@/hooks'
import { fetchOnboardingStatus, sendOnboardingCommand, onboardingBack, onboardingRestartStep, onboardingResendCode, onboardingReset } from '@/api'
import { StatusDot, ConfirmDialog } from '@/components'

export function SetupRecovery() {
  const { data, refetch } = usePolling({ fetcher: fetchOnboardingStatus, interval: 10000 })
  const [cmdResult, setCmdResult] = useState<string | null>(null)
  const [resetConfirm, setResetConfirm] = useState(false)

  const runCommand = async (fn: () => Promise<{ response: string }>) => {
    const res = await fn()
    setCmdResult(res.response)
    refetch()
  }

  const handleReset = async () => {
    const res = await onboardingReset()
    setCmdResult(res.response)
    setResetConfirm(false)
    refetch()
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Setup & Recovery</h2>

      {/* Status Overview */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          Onboarding Status
        </h3>
        {!data ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <span className="text-[10px] uppercase tracking-wider text-text-muted">State</span>
              <div className="mt-1">
                <StatusDot state={data.is_ready ? 'healthy' : 'degraded'} label={data.state} />
              </div>
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-text-muted">Provider</span>
              <p className="text-sm font-mono text-text-primary mt-1">{data.flagship_provider || 'None'}</p>
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-text-muted">Credentials</span>
              <p className="text-sm font-mono text-text-primary mt-1">{data.credential_status}</p>
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-text-muted">Local Model</span>
              <p className="text-sm font-mono text-text-primary mt-1">{data.local_model_status}</p>
            </div>
          </div>
        )}
        {data?.cooldown_active && (
          <div className="mt-4 p-3 bg-state-degraded/10 border border-state-degraded/30 rounded">
            <span className="text-xs font-semibold text-state-degraded">
              Cooldown Active — {Math.round(data.cooldown_remaining)}s remaining
            </span>
            {data.last_error && <p className="text-xs text-text-secondary mt-1">{data.last_error}</p>}
          </div>
        )}
      </section>

      {/* Recovery Commands */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          Recovery Commands
        </h3>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => runCommand(() => sendOnboardingCommand('STATUS'))} className="px-3 py-2 text-sm bg-surface-input border border-border-default rounded-md text-text-secondary hover:text-text-primary hover:bg-surface-card-elevated transition-colors">
            Check Status
          </button>
          <button onClick={() => runCommand(onboardingBack)} className="px-3 py-2 text-sm bg-surface-input border border-border-default rounded-md text-text-secondary hover:text-text-primary hover:bg-surface-card-elevated transition-colors">
            Go Back
          </button>
          <button onClick={() => runCommand(onboardingRestartStep)} className="px-3 py-2 text-sm bg-surface-input border border-border-default rounded-md text-text-secondary hover:text-text-primary hover:bg-surface-card-elevated transition-colors">
            Restart Step
          </button>
          <button onClick={() => runCommand(onboardingResendCode)} className="px-3 py-2 text-sm bg-surface-input border border-border-default rounded-md text-text-secondary hover:text-text-primary hover:bg-surface-card-elevated transition-colors">
            Resend Code
          </button>
          <button onClick={() => setResetConfirm(true)} className="px-3 py-2 text-sm bg-surface-input border border-state-error/30 rounded-md text-state-error hover:bg-state-error/10 transition-colors">
            Reset Onboarding
          </button>
        </div>
      </section>

      {/* Command Result */}
      {cmdResult && (
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Command Result
          </h3>
          <pre className="text-sm font-mono text-text-primary bg-surface-input rounded p-3 whitespace-pre-wrap">
            {cmdResult}
          </pre>
        </section>
      )}

      <ConfirmDialog
        open={resetConfirm}
        title="Reset Onboarding"
        description="This will clear all onboarding progress and restart from scratch. This action cannot be undone."
        variant="destructive"
        confirmLabel="Reset"
        onConfirm={handleReset}
        onCancel={() => setResetConfirm(false)}
      />
    </div>
  )
}
