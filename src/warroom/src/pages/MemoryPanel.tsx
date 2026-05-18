import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, Database, Gauge, Search, ShieldCheck } from 'lucide-react'
import { usePolling, usePageTitle } from '@/hooks'
import { fetchCoreBlocks, fetchQuarantine, fetchMemoryStats, fetchRecentMemory, searchMemory, approveQuarantinedItem } from '@/api'
import { EmptyState } from '@/components'
import type { CoreBlock, SearchResultItem, RecentMemoryItem, QuarantineItem } from '@/types/api'
import { quarantineBadgeClass, quarantineReviewSummary } from '@/utils/memoryReview'

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
  return `${block.token_count} tokens - ${renderBlockContent(block).length} chars`
}

type MemoryOverviewTone = 'accent' | 'healthy' | 'warning' | 'muted'

const overviewToneClass: Record<MemoryOverviewTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function MemoryOverviewTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: MemoryOverviewTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${overviewToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
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
  const [quarantineExpanded, setQuarantineExpanded] = useState(false)

  const memoryDisabled = blocksError != null && quarantineError != null

  const coreBlocks = blocks?.blocks ?? {}
  const totalTokens = blocks?.total_tokens ?? 0
  const quarantineItems = quarantine?.items ?? []
  const recentItems = recent?.items ?? []
  const tierCounts = stats?.index.items_by_tier ?? {}
  const coreBlockCount = Object.keys(coreBlocks).length
  const tieredTotal = (tierCounts.working ?? 0) + (tierCounts.episodic ?? 0) + (tierCounts.archival ?? 0)

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    try {
      const res = await searchMemory(searchQuery)
      setSearchResults(res.results)
    } catch {
      setSearchResults([])
    }
  }

  const handlePromote = async (item: QuarantineItem) => {
    await approveQuarantinedItem(item.tier, item.id, 'War Room', 'Approved from Memory panel')
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
    <div className="space-y-6">
      <div className="rounded-lg border border-border-default bg-surface-card p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-accent-primary">
              Memory Overview
            </div>
            <h2 className="mt-2 text-2xl font-semibold text-text-primary">Memory</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Inspect durable core blocks, tiered memory activity, quarantine pressure, and search results without
              entering the governed edit workflow.
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            <Link
              to="/memory/manage"
              className="inline-flex items-center gap-2 px-4 py-2 text-sm rounded-md bg-accent-primary text-white hover:bg-accent-primary/80"
            >
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              Governed Manager
            </Link>
            <Link
              to="/memory/context"
              className="inline-flex items-center gap-2 px-4 py-2 text-sm rounded-md border border-border-default text-text-primary hover:border-border-active"
            >
              <Gauge className="h-4 w-4" aria-hidden="true" />
              Context Efficiency
            </Link>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <MemoryOverviewTile
            label="Core Blocks"
            value={coreBlockCount}
            detail={`${totalTokens.toLocaleString()} core tokens`}
            tone="accent"
          />
          <MemoryOverviewTile
            label="Tiered Items"
            value={tieredTotal.toLocaleString()}
            detail="Indexed working, episodic, and archival records."
          />
          <MemoryOverviewTile
            label="Working"
            value={(tierCounts.working ?? 0).toLocaleString()}
            detail="Near-term active memory."
            tone="warning"
          />
          <MemoryOverviewTile
            label="Episodic"
            value={(tierCounts.episodic ?? 0).toLocaleString()}
            detail="Event and session memory."
          />
          <MemoryOverviewTile
            label="Archival"
            value={(tierCounts.archival ?? 0).toLocaleString()}
            detail="Long-retention knowledge."
            tone="healthy"
          />
        </div>
      </div>

      <div className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="mb-3 flex items-center gap-2">
          <Search className="h-4 w-4 text-accent-primary" aria-hidden="true" />
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Memory Search</h3>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            type="text"
            placeholder="Search memory..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            className="flex-1 bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-active"
          />
          <button onClick={handleSearch} className="inline-flex items-center justify-center gap-2 px-4 py-2 bg-accent-primary text-white text-sm rounded-md hover:bg-accent-primary/80">
            <Search className="h-4 w-4" aria-hidden="true" />
            Search
          </button>
        </div>
      </div>

      {searchResults.length > 0 && (
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Search Results</h3>
              <p className="mt-1 text-xs text-text-muted">Ranked memory matches for the current query.</p>
            </div>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
              {searchResults.length}
            </span>
          </div>
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
                  {result.namespace} {result.tags.length > 0 ? `- ${result.tags.join(', ')}` : ''}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="bg-surface-card border border-border-default rounded-lg p-4">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-state-warning" aria-hidden="true" />
              <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Quarantine Queue</h3>
            </div>
            <p className="mt-1 text-xs text-text-muted">
              Inspection view for memory awaiting review. Use the manager for full governed review controls.
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
              {quarantineItems.length}
            </span>
            <button
              onClick={() => setQuarantineExpanded((current) => !current)}
              className="px-2 py-1 text-[10px] font-medium rounded border border-border-default text-text-secondary hover:text-text-primary hover:border-border-active"
            >
              {quarantineExpanded ? 'Collapse' : 'Expand'}
            </button>
          </div>
        </div>
        {!quarantineExpanded ? (
          <div className="rounded-md border border-border-default bg-surface-card-elevated px-3 py-2 text-sm text-text-muted">
            {quarantineItems.length === 0
              ? 'No quarantined memory items.'
              : `${quarantineItems.length} quarantined memory item${quarantineItems.length === 1 ? '' : 's'} hidden from the overview.`}
          </div>
        ) : quarantineItems.length === 0 ? (
          <p className="text-sm text-text-muted">No quarantined items</p>
        ) : (
          <div className="space-y-3">
            {quarantineItems.map((item) => {
              const review = quarantineReviewSummary(item)
              return (
                <div key={item.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm text-text-primary truncate">{item.title}</span>
                        <span className={`rounded border px-2 py-0.5 text-[10px] font-medium ${quarantineBadgeClass(review.tone)}`}>
                          {review.label}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-text-muted">{review.description}</p>
                    </div>
                    <span className="text-xs font-mono text-state-degraded">{item.status}</span>
                  </div>
                  {review.details.length > 0 ? (
                    <div className="mt-3 rounded border border-border-default bg-surface-input px-3 py-2">
                      {review.details.slice(0, 2).map((detail) => (
                        <div key={detail} className="text-[10px] text-text-secondary">
                          {detail}
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <p className="text-xs text-text-secondary mt-2 whitespace-pre-wrap break-words">{item.content}</p>
                  <button
                    onClick={() => handlePromote(item)}
                    className="mt-2 px-3 py-1 text-xs bg-state-healthy/15 text-state-healthy rounded hover:bg-state-healthy/25"
                  >
                    Promote
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <div className="flex items-start justify-between gap-3 mb-3">
            <div>
              <div className="flex items-center gap-2">
                <Database className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                  Core Blocks
                </h3>
              </div>
              <p className="mt-1 text-xs text-text-muted">Persistent constitution memory blocks and token budgets.</p>
            </div>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
              {totalTokens.toLocaleString()} tokens
            </span>
          </div>
          {coreBlockCount === 0 ? (
            <p className="text-sm text-text-muted">No core blocks loaded</p>
          ) : (
            <div className="space-y-3">
              {Object.entries(coreBlocks).map(([type, block]) => (
                <div key={type} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-mono text-text-primary">{type}</div>
                      <div className="text-[10px] font-mono text-text-muted mt-1">
                        {blockSizeLabel(block)} - budget {block.token_budget}
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
          <div className="flex items-start justify-between gap-3 mb-3">
            <div>
              <div className="flex items-center gap-2">
                <Activity className="h-4 w-4 text-state-healthy" aria-hidden="true" />
                <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                  Recent Tiered Memory
                </h3>
              </div>
              <p className="mt-1 text-xs text-text-muted">Latest indexed working, episodic, and archival records.</p>
            </div>
            <div className="flex items-center gap-3">
              <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
                {recentItems.length} shown
              </span>
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
