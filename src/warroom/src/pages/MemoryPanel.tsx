import { useState } from 'react'
import { usePolling } from '@/hooks'
import { fetchCoreBlocks, fetchQuarantine, fetchMemoryStats, searchMemory, promoteItem } from '@/api'
import { MetricCard } from '@/components'

export function MemoryPanel() {
  const { data: blocks } = usePolling({ fetcher: fetchCoreBlocks, interval: 30000 })
  const { data: quarantine, refetch: refetchQuarantine } = usePolling({ fetcher: fetchQuarantine, interval: 30000 })
  const { data: stats } = usePolling({ fetcher: fetchMemoryStats, interval: 60000 })
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<Array<{ id: string; title: string; content: string; tier: string; confidence: number }>>([])

  const coreBlocks = blocks?.blocks ?? {}
  const totalTokens = blocks?.total_tokens ?? 0
  const quarantineItems = quarantine?.items ?? []

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    const res = await searchMemory(searchQuery)
    setSearchResults(res.results)
  }

  const handlePromote = async (itemId: string) => {
    await promoteItem(itemId)
    refetchQuarantine()
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Memory</h2>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard label="Core Blocks" value={Object.keys(coreBlocks).length} />
        <MetricCard label="Total Tokens" value={totalTokens.toLocaleString()} />
        <MetricCard label="Quarantined" value={quarantineItems.length} />
        <MetricCard label="Index Size" value={(stats?.index as Record<string, unknown>)?.total_items?.toString() ?? '--'} />
      </div>

      {/* Search */}
      <div className="mb-6 flex gap-2">
        <input
          type="text"
          placeholder="Search memory..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          className="flex-1 bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-active"
        />
        <button onClick={handleSearch} className="px-4 py-2 bg-accent-primary text-white text-sm rounded-md hover:bg-accent-primary/80">
          Search
        </button>
      </div>

      {searchResults.length > 0 && (
        <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Search Results ({searchResults.length})
          </h3>
          <div className="space-y-2">
            {searchResults.map((r) => (
              <div key={r.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-accent-primary">{r.tier}</span>
                  <span className="text-sm font-medium text-text-primary">{r.title}</span>
                  <span className="text-[10px] text-text-muted font-mono ml-auto">{(r.confidence * 100).toFixed(0)}%</span>
                </div>
                <p className="text-xs text-text-secondary mt-1">{r.content}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Core Blocks */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Core Blocks
          </h3>
          <div className="space-y-3">
            {Object.entries(coreBlocks).map(([type, block]) => (
              <div key={type} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-mono text-text-primary">{type}</span>
                  <span className="text-xs font-mono text-text-muted">
                    {block.token_count}/{block.token_budget} tokens
                  </span>
                </div>
                <div className="mt-1 w-full h-1 rounded-full bg-surface-input overflow-hidden">
                  <div
                    className="h-full rounded-full bg-accent-primary transition-all"
                    style={{ width: `${Math.min(100, (block.token_count / block.token_budget) * 100)}%` }}
                  />
                </div>
                <div className="flex items-center gap-2 mt-1 text-[10px] text-text-muted">
                  <span>v{block.version}</span>
                  <span>{block.status}</span>
                  <span className="ml-auto">{block.updated_by}</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Quarantine */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Quarantine Queue ({quarantineItems.length})
          </h3>
          {quarantineItems.length === 0 ? (
            <p className="text-sm text-text-muted">No quarantined items</p>
          ) : (
            <div className="space-y-3">
              {quarantineItems.map((item) => (
                <div key={item.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-text-primary">{item.title}</span>
                    <span className="text-xs font-mono text-state-degraded">{item.status}</span>
                  </div>
                  <p className="text-xs text-text-secondary mt-1">{item.content}</p>
                  <button
                    onClick={() => handlePromote(item.id)}
                    className="mt-2 px-3 py-1 text-xs bg-state-healthy/15 text-state-healthy rounded hover:bg-state-healthy/25"
                  >
                    Promote
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
