export function CommandCenter() {
  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Command Center</h2>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: 2/3 width */}
        <div className="lg:col-span-2 space-y-6">
          {/* Active Task Monitor — WR-23 */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Active Task</h3>
            <p className="text-sm text-text-muted">No active task</p>
          </section>

          {/* Chat Interface — WR-7 */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4 min-h-[300px] flex flex-col">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Command Interface</h3>
            <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
              Chat interface will be wired in WR-7
            </div>
            <div className="mt-4 flex gap-2">
              <input
                type="text"
                placeholder="Issue command to Lancelot..."
                className="flex-1 bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-active"
                disabled
              />
              <button
                disabled
                className="px-4 py-2 bg-accent-primary text-white text-sm font-medium rounded-md opacity-50 cursor-not-allowed"
              >
                Send
              </button>
            </div>
          </section>

          {/* Activity Feed — WR-10 */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Recent Activity</h3>
            <p className="text-sm text-text-muted">Activity feed will be wired in WR-10</p>
          </section>
        </div>

        {/* Right column: 1/3 width */}
        <div className="space-y-6">
          {/* Pending Actions — WR-15 */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Pending Actions</h3>
            <p className="text-sm text-text-muted">No pending actions</p>
          </section>

          {/* Controls — WR-8 */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Controls</h3>
            <div className="space-y-2">
              <button
                disabled
                className="w-full px-3 py-2 text-sm text-left bg-surface-input border border-border-default rounded-md text-text-muted cursor-not-allowed"
              >
                Crusader Mode — Off
              </button>
              <button
                disabled
                className="w-full px-3 py-2 text-sm text-left bg-surface-input border border-border-default rounded-md text-text-muted cursor-not-allowed"
              >
                Pause Agent
              </button>
              <button
                disabled
                className="w-full px-3 py-2 text-sm text-left bg-surface-input border border-border-default rounded-md text-state-error/50 cursor-not-allowed"
              >
                Emergency Stop
              </button>
            </div>
          </section>

          {/* Quick Stats — WR-11 */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Quick Stats</h3>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Actions Today</span>
                <p className="text-xl font-mono font-bold text-text-primary">--</p>
              </div>
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Pending</span>
                <p className="text-xl font-mono font-bold text-text-primary">--</p>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
