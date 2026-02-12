import { usePolling } from '@/hooks'
import { fetchSoulStatus } from '@/api'

export function SoulInspector() {
  const { data, loading } = usePolling({ fetcher: fetchSoulStatus, interval: 30000 })

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Soul Inspector</h2>

      {loading && !data ? (
        <p className="text-text-muted text-sm">Loading soul data...</p>
      ) : !data?.active_version ? (
        <div className="bg-surface-card border border-border-default rounded-lg p-6 text-center">
          <p className="text-text-muted">Soul system not initialized</p>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Active Version */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Active Version
            </h3>
            <div className="flex items-center gap-3">
              <span className="w-2 h-2 rounded-full bg-state-healthy" />
              <span className="text-lg font-mono font-bold text-text-primary">
                {data.active_version}
              </span>
            </div>
          </section>

          {/* Available Versions */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Available Versions ({data.available_versions.length})
            </h3>
            <div className="space-y-2">
              {data.available_versions.map((v) => (
                <div
                  key={v}
                  className={`flex items-center gap-2 px-3 py-2 rounded text-sm ${
                    v === data.active_version
                      ? 'bg-accent-primary/10 border border-accent-primary/30 text-accent-primary'
                      : 'bg-surface-card-elevated text-text-secondary'
                  }`}
                >
                  <span className="font-mono">{v}</span>
                  {v === data.active_version && (
                    <span className="text-[10px] uppercase tracking-wider font-semibold">ACTIVE</span>
                  )}
                </div>
              ))}
            </div>
          </section>

          {/* Pending Proposals */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Pending Proposals ({data.pending_proposals.length})
            </h3>
            {data.pending_proposals.length === 0 ? (
              <p className="text-sm text-text-muted">No pending amendments</p>
            ) : (
              <div className="space-y-3">
                {data.pending_proposals.map((p) => (
                  <div key={p.proposal_id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-text-muted">{p.proposal_id}</span>
                      <span className="text-xs px-1.5 py-0.5 rounded bg-state-degraded/15 text-state-degraded">
                        {p.status}
                      </span>
                    </div>
                    <pre className="mt-2 text-xs font-mono text-text-secondary bg-surface-input rounded p-2 overflow-auto max-h-40">
                      {JSON.stringify(p, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
