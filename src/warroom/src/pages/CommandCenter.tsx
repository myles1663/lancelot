import { ChatInterface } from './command/ChatInterface'
import { ControlsPanel } from './command/ControlsPanel'

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

          {/* Chat Interface */}
          <ChatInterface />

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

          {/* Controls Panel */}
          <ControlsPanel />

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
