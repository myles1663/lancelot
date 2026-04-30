interface SessionExpiryModalProps {
  remainingSeconds: number
  onStay: () => void
  onSignOut: () => void
}

export function SessionExpiryModal({
  remainingSeconds,
  onStay,
  onSignOut,
}: SessionExpiryModalProps) {
  const minutes = Math.max(0, Math.ceil(remainingSeconds / 60))

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60">
      <div className="bg-surface-card border border-border-default rounded-xl p-6 max-w-sm w-full mx-4 shadow-2xl">
        <h2 className="text-base font-semibold text-text-primary mb-2">
          Session Expiring
        </h2>
        <p className="text-sm text-text-secondary mb-6">
          Your session will expire in{' '}
          <span className="text-state-degraded font-medium">
            {minutes} minute{minutes !== 1 ? 's' : ''}
          </span>
          . Would you like to stay signed in?
        </p>
        <div className="flex gap-3 justify-end">
          <button
            onClick={onSignOut}
            className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary border border-border-default rounded-lg transition-colors"
          >
            Sign Out
          </button>
          <button
            onClick={onStay}
            className="px-4 py-2 text-sm text-white bg-accent-primary hover:bg-accent-primary/90 rounded-lg transition-colors"
          >
            Stay Signed In
          </button>
        </div>
      </div>
    </div>
  )
}
