import { useState } from 'react'
import { Link } from 'react-router-dom'
import { usePolling, usePageTitle } from '@/hooks'
import { fetchCoreBlocks, fetchQuarantine, fetchMemoryStats, fetchRecentMemory, searchMemory, promoteItem } from '@/api'
import { MetricCard, EmptyState } from '@/components'
import type { CoreBlock, SearchResultItem, RecentMemoryItem } from '@/types/api'

function formatTimestamp(value: string): string {
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function tierTone(tier: string): string {
  switch (tier) {
    case 'working':
      return 'text-state-warning'
    case 'episodic':
      return 'text-accent-primary'
    case 'archival':
      return 'text-state-healthy'
    default:
      return 'text-text-muted'
  }
}

function renderBlockContent(block: CoreBlock): string {
  return block.content.trim() || 'No content stored in this block yet.'
}

function blockSizeLabel(block: CoreBlock): string {
  return `${block.token_count} tokens • ${renderBlockContent(block).length} chars`
}

export function MemoryPanel() {
  usePageTitle('Memory')

  const { data: blocks, error: blocksError } = usePolling({ fetcher: fetchCoreBlocks, interval: 30000 })
  const { data: quarantine, error: quarantineError, refetch: refetchQuarantine } = usePolling({ fetcher: fetchQuarantine, interval: 30000 })
  const { data: stats } = usePolling({ fetcher: fetchMemoryStats, interval: 60000 })
  const { data: recent } = usePolling({ fetcher: () => fetchRecentMemory(12), interval: 30000 })

  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResultItem[]>([])
  const [expandedBlocks, setExpandedBlocks] = useState<Record<string, boolean>>({})
  const [recentExpanded, setRecentExpanded] = useState(false)

  const memoryDisabled = blocksError != null && quarantineError != null

  const coreBlocks = blocks?.blocks ?? {}
  const totalTokens = blocks?.total_tokens ?? 0
  const quarantineItems = quarantine?.items ?? []
  const recentItems = recent?.items ?? []
  const tierCounts = stats?.index.items_by_tier ?? {}

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    try {
      const res = await searchMemory(searchQuery)
      setSearchResults(res.results)
    } catch {
      setSearchResults([])
    }
  }

  const handlePromote = async (itemId: string) => {
    await promoteItem(itemId)
    refetchQuarantine()
  }

  const toggleBlock = (blockType: string) => {
    setExpandedBlocks((current) => ({
      ...current,
      [blockType]: !current[blockType],
    }))
  }

  if (memoryDisabled) {
    return (
      <div>
        <h2 className="text-lg font-semibold text-text-primary mb-6">Memory</h2>
        <EmptyState
          title="Structured Memory Disabled"
          description="The structured memory subsystem is not enabled. Set FEATURE_MEMORY_VNEXT=true in your .env file and restart the container to activate tiered memory with core blocks, context compilation, and governed self-edits."
          icon="&#128451;"
        />
      </div>
    )
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-2">Memory</h2>
      <p className="text-sm text-text-muted mb-6">
        Core block tokens reflect only the five persistent constitution blocks. Tier counts below reflect indexed working,
        episodic, and archival memory items.
      </p>

      <div className="flex flex-wrap items-center gap-3 mb-6">
        <Link
          to="/memory/manage"
          className="inline-flex items-center px-4 py-2 text-sm rounded-md bg-accent-primary text-white hover:bg-accent-primary/80"
        >
          Open Governed Memory Manager
        </Link>
        <span className="text-xs text-text-muted">
          Use the manager for governed edits, removals, quarantine review, and future rollback tooling.
        </span>
      </div>

      <div className="grid grid-cols-2 xl:grid-cols-5 gap-4 mb-6">
        <MetricCard label="Core Blocks" value={Object.keys(coreBlocks).length} />
        <MetricCard label="Core Tokens" value={totalTokens.toLocaleString()} />
        <MetricCard label="Working" value={(tierCounts.working ?? 0).toLocaleString()} />
        <MetricCard label="Episodic" value={(tierCounts.episodic ?? 0).toLocaleString()} />
        <MetricCard label="Archival" value={(tierCounts.archival ?? 0).toLocaleString()} />
      </div>

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
            {searchResults.map((result) => (
              <div key={result.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-mono ${tierTone(result.tier)}`}>{result.tier}</span>
                  <span className="text-sm font-medium text-text-primary">{result.title}</span>
                  <span className="text-[10px] text-text-muted font-mono ml-auto">{(result.confidence * 100).toFixed(0)}%</span>
                </div>
                <p className="text-xs text-text-secondary mt-1 whitespace-pre-wrap break-words">{result.content}</p>
                <div className="mt-2 text-[10px] font-mono text-text-muted">
                  {result.namespace} {result.tags.length > 0 ? `• ${result.tags.join(', ')}` : ''}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
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
                <p className="text-xs text-text-secondary mt-1 whitespace-pre-wrap break-words">{item.content}</p>
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

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
              Core Blocks
            </h3>
            <span className="text-xs font-mono text-text-muted">{totalTokens.toLocaleString()} total tokens</span>
          </div>
          {Object.keys(coreBlocks).length === 0 ? (
            <p className="text-sm text-text-muted">No core blocks loaded</p>
          ) : (
            <div className="space-y-3">
              {Object.entries(coreBlocks).map(([type, block]) => (
                <div key={type} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-mono text-text-primary">{type}</div>
                      <div className="text-[10px] font-mono text-text-muted mt-1">
                        {blockSizeLabel(block)} • budget {block.token_budget}
                      </div>
                    </div>
                    <button
                      onClick={() => toggleBlock(type)}
                      className="px-2 py-1 text-[10px] font-medium rounded border border-border-default text-text-secondary hover:text-text-primary hover:border-border-active"
                    >
                      {expandedBlocks[type] ? 'Collapse' : 'Expand'}
                    </button>
                  </div>
                  <div className="mt-1 w-full h-1 rounded-full bg-surface-input overflow-hidden">
                    <div
                      className="h-full rounded-full bg-accent-primary transition-all"
                      style={{ width: `${Math.min(100, (block.token_count / block.token_budget) * 100)}%` }}
                    />
                  </div>
                  {expandedBlocks[type] ? (
                    <pre className="mt-3 text-xs text-text-secondary whitespace-pre-wrap break-words font-sans leading-5">
                      {renderBlockContent(block)}
                    </pre>
                  ) : null}
                  <div className="flex flex-wrap items-center gap-2 mt-3 text-[10px] text-text-muted">
                    <span>v{block.version}</span>
                    <span>{block.status}</span>
                    <span>{block.updated_by}</span>
                    <span>{formatTimestamp(block.updated_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
              Recent Tiered Memory
            </h3>
            <div className="flex items-center gap-3">
              <span className="text-xs font-mono text-text-muted">{recentItems.length} shown</span>
              <button
                onClick={() => setRecentExpanded((current) => !current)}
                className="px-2 py-1 text-[10px] font-medium rounded border border-border-default text-text-secondary hover:text-text-primary hover:border-border-active"
              >
                {recentExpanded ? 'Collapse' : 'Expand'}
              </button>
            </div>
          </div>
          {recentItems.length === 0 ? (
            <p className="text-sm text-text-muted">No working, episodic, or archival items have been indexed yet.</p>
          ) : recentExpanded ? (
            <div className="space-y-3">
              {recentItems.map((item: RecentMemoryItem) => (
                <div key={item.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-mono ${tierTone(item.tier)}`}>{item.tier}</span>
                    <span className="text-sm font-medium text-text-primary">{item.title}</span>
                    <span className="text-[10px] text-text-muted font-mono ml-auto">{item.token_count} tokens</span>
                  </div>
                  <p className="text-xs text-text-secondary mt-2 whitespace-pre-wrap break-words">{item.content}</p>
                  <div className="flex flex-wrap items-center gap-2 mt-3 text-[10px] text-text-muted">
                    <span>{item.namespace}</span>
                    <span>{formatTimestamp(item.updated_at)}</span>
                    <span>{(item.confidence * 100).toFixed(0)}%</span>
                    {item.tags.length > 0 ? <span>{item.tags.join(', ')}</span> : null}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="space-y-2">
              {recentItems.map((item: RecentMemoryItem) => (
                <div key={item.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-mono ${tierTone(item.tier)}`}>{item.tier}</span>
                    <span className="text-sm font-medium text-text-primary truncate">{item.title}</span>
                    <span className="text-[10px] text-text-muted font-mono ml-auto">{item.token_count} tokens</span>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 mt-2 text-[10px] text-text-muted">
                    <span>{item.namespace}</span>
                    <span>{formatTimestamp(item.updated_at)}</span>
                    <span>{(item.confidence * 100).toFixed(0)}%</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
