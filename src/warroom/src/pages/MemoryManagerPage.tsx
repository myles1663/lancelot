import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { usePageTitle, usePolling } from '@/hooks'
import {
  fetchCoreBlocks,
  fetchQuarantine,
  fetchRecentMemory,
  fetchMemoryCommits,
  beginCommit,
  addEdit,
  finishCommit,
  rollbackCommit,
  approveQuarantinedItem,
  rejectQuarantinedItem,
  approveQuarantinedCoreBlock,
  rejectQuarantinedCoreBlock,
  updateMemoryItemStatus,
  deleteMemoryItem,
} from '@/api'
import { validateSession } from '@/api/auth'
import { ConfirmDialog, EmptyState } from '@/components'
import type { CoreBlock, MemoryCommitSummary, QuarantineItem, RecentMemoryItem } from '@/types/api'
import { emitWarRoomNotification } from '@/utils/notifications'
import { quarantineBadgeClass, quarantineReviewSummary } from '@/utils/memoryReview'

type ConfirmState = {
  title: string
  description: string
  confirmLabel: string
  variant?: 'default' | 'destructive'
  onConfirm: () => Promise<unknown>
}

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

function blockPreview(block: CoreBlock | undefined): string {
  if (!block) return ''
  return block.content.trim() || 'No content stored in this block yet.'
}

export function MemoryManagerPage() {
  usePageTitle('Governed Memory Manager')

  const { data: blocks, error: blocksError, refetch: refetchBlocks } = usePolling({
    fetcher: fetchCoreBlocks,
    interval: 30000,
  })
  const { data: quarantine, refetch: refetchQuarantine } = usePolling({
    fetcher: fetchQuarantine,
    interval: 15000,
  })
  const { data: recent, refetch: refetchRecent } = usePolling({
    fetcher: () => fetchRecentMemory(24),
    interval: 30000,
  })
  const { data: commits, refetch: refetchCommits } = usePolling({
    fetcher: () => fetchMemoryCommits(20),
    interval: 15000,
  })

  const [operatorName, setOperatorName] = useState('operator')
  const [selectedBlock, setSelectedBlock] = useState('mission')
  const [editorContent, setEditorContent] = useState('')
  const [commitMessage, setCommitMessage] = useState('')
  const [editReason, setEditReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null)

  const coreBlocks = blocks?.blocks ?? {}
  const currentBlock = coreBlocks[selectedBlock]
  const quarantinedCoreBlocks = quarantine?.core_blocks ?? []
  const quarantinedItems = quarantine?.items ?? []
  const recentItems = recent?.items ?? []
  const recentCommits = commits?.commits ?? []

  const blockOptions = useMemo(() => Object.keys(coreBlocks), [coreBlocks])

  useEffect(() => {
    let active = true
    validateSession()
      .then((session) => {
        if (active && session.username) setOperatorName(session.username)
      })
      .catch(() => {
        if (active) {
          emitWarRoomNotification('Operator identity lookup failed; using generic operator name.', 'normal')
        }
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (!blockOptions.length) return
    if (!blockOptions.includes(selectedBlock)) {
      const nextBlock = blockOptions[0]
      if (nextBlock) setSelectedBlock(nextBlock)
    }
  }, [blockOptions, selectedBlock])

  useEffect(() => {
    if (!currentBlock) return
    setEditorContent(currentBlock.content)
  }, [selectedBlock, currentBlock?.content])

  const refreshAll = async () => {
    await Promise.all([refetchBlocks(), refetchQuarantine(), refetchRecent(), refetchCommits()])
  }

  const runAction = async (action: () => Promise<unknown>, successMessage: string) => {
    setBusy(true)
    setError(null)
    setSuccess(null)
    try {
      await action()
      setSuccess(successMessage)
      await refreshAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const handleSaveCoreBlock = async () => {
    if (!selectedBlock || !editReason.trim()) {
      setError('Select a block and provide a reason before saving.')
      return
    }
    await runAction(async () => {
      const commit = await beginCommit(operatorName, commitMessage.trim() || `Governed update for ${selectedBlock}`)
      await addEdit(commit.commit_id, {
        op: 'replace',
        target: `core:${selectedBlock}`,
        after: editorContent,
        reason: editReason.trim(),
        confidence: 1,
        editor: 'owner',
        provenance_type: 'system',
        provenance_ref: 'warroom-memory-manager',
      })
      await finishCommit(commit.commit_id)
      setCommitMessage('')
      setEditReason('')
    }, `Saved governed update to ${selectedBlock}.`)
  }

  const requestConfirm = (state: ConfirmState) => setConfirmState(state)

  const handleConfirm = async () => {
    if (!confirmState) return
    const action = confirmState.onConfirm
    setConfirmState(null)
    await runAction(action, 'Governed memory action completed.')
  }

  if (blocksError) {
    return (
      <div>
        <div className="flex items-center justify-between gap-4 mb-6">
          <div>
            <h2 className="text-lg font-semibold text-text-primary">Governed Memory Manager</h2>
            <p className="text-sm text-text-muted mt-2">
              This page manages governed memory actions separately from the lightweight Memory overview.
            </p>
          </div>
          <Link
            to="/memory"
            className="inline-flex items-center px-4 py-2 text-sm rounded-md border border-border-default text-text-primary hover:border-border-active"
          >
            Back to Memory
          </Link>
        </div>
        <EmptyState
          title="Governed Memory Unavailable"
          description="The memory subsystem is not currently available to the manager page."
          icon="&#128451;"
        />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Governed Memory Manager</h2>
          <p className="text-sm text-text-muted mt-2">
            Operator-scoped memory actions live here so the main Memory page stays focused on inspection.
          </p>
        </div>
        <Link
          to="/memory"
          className="inline-flex items-center px-4 py-2 text-sm rounded-md border border-border-default text-text-primary hover:border-border-active"
        >
          Back to Memory
        </Link>
      </div>

      <div className="flex flex-wrap items-center gap-3 text-xs text-text-muted font-mono">
        <span>operator {operatorName}</span>
        <span>quarantine {quarantinedCoreBlocks.length + quarantinedItems.length}</span>
        <span>recent items {recentItems.length}</span>
        <span>recent commits {recentCommits.length}</span>
      </div>

      {error ? (
        <div className="rounded-lg border border-state-error/40 bg-state-error/10 px-4 py-3 text-sm text-state-error">
          {error}
        </div>
      ) : null}

      {success ? (
        <div className="rounded-lg border border-state-healthy/40 bg-state-healthy/10 px-4 py-3 text-sm text-state-healthy">
          {success}
        </div>
      ) : null}

      <section className="bg-surface-card border border-border-default rounded-lg p-4">
        <div className="flex items-center justify-between gap-3 mb-4">
          <div>
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Core Block Editor</h3>
            <p className="text-xs text-text-muted mt-1">
              Governed owner edits flow through a committed memory change, not an inline overwrite.
            </p>
          </div>
          <span className="text-xs font-mono text-text-muted">
            {currentBlock ? `${currentBlock.token_count} / ${currentBlock.token_budget} tokens` : 'No block selected'}
          </span>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[220px_minmax(0,1fr)] gap-4">
          <div className="space-y-3">
            <label className="block text-xs font-medium uppercase tracking-wider text-text-secondary">
              Block
            </label>
            <div className="space-y-2">
              {blockOptions.map((blockType) => (
                <button
                  key={blockType}
                  onClick={() => setSelectedBlock(blockType)}
                  className={`w-full text-left rounded-md border px-3 py-2 text-sm transition-colors ${
                    selectedBlock === blockType
                      ? 'border-accent-primary bg-accent-primary/10 text-text-primary'
                      : 'border-border-default bg-surface-card-elevated text-text-secondary hover:border-border-active'
                  }`}
                >
                  <div className="font-mono text-xs">{blockType}</div>
                  <div className="text-[10px] text-text-muted mt-1 truncate">
                    {blockPreview(coreBlocks[blockType])}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs font-medium uppercase tracking-wider text-text-secondary">Commit Message</span>
                <input
                  value={commitMessage}
                  onChange={(e) => setCommitMessage(e.target.value)}
                  placeholder="Governed core memory update"
                  className="mt-2 w-full bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary"
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium uppercase tracking-wider text-text-secondary">Reason</span>
                <input
                  value={editReason}
                  onChange={(e) => setEditReason(e.target.value)}
                  placeholder="Why this memory should change"
                  className="mt-2 w-full bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary"
                />
              </label>
            </div>

            <label className="block">
              <span className="text-xs font-medium uppercase tracking-wider text-text-secondary">Content</span>
              <textarea
                value={editorContent}
                onChange={(e) => setEditorContent(e.target.value)}
                rows={12}
                className="mt-2 w-full bg-surface-input border border-border-default rounded-md px-3 py-3 text-sm text-text-primary font-sans leading-6"
              />
            </label>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-xs text-text-muted">
                Current block version {currentBlock?.version ?? 0} • updated {currentBlock ? formatTimestamp(currentBlock.updated_at) : 'n/a'}
              </div>
              <button
                onClick={handleSaveCoreBlock}
                disabled={busy || !currentBlock}
                className="px-4 py-2 text-sm rounded-md bg-accent-primary text-white hover:bg-accent-primary/80 disabled:opacity-50"
              >
                {busy ? 'Saving…' : 'Commit Core Block Edit'}
              </button>
            </div>
          </div>
        </div>
      </section>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-4">
            Quarantined Core Blocks ({quarantinedCoreBlocks.length})
          </h3>
          {quarantinedCoreBlocks.length === 0 ? (
            <p className="text-sm text-text-muted">No quarantined core blocks.</p>
          ) : (
            <div className="space-y-3">
              {quarantinedCoreBlocks.map((block) => (
                <div key={block.block_type} className="rounded-md border border-border-default bg-surface-card-elevated p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-mono text-text-primary">{block.block_type}</div>
                      <div className="text-[10px] text-text-muted mt-1">{formatTimestamp(block.updated_at)}</div>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() =>
                          runAction(
                            () => approveQuarantinedCoreBlock(block.block_type, operatorName, 'Approved from War Room'),
                            `Approved quarantined core block ${block.block_type}.`,
                          )
                        }
                        className="px-3 py-1 text-xs rounded bg-state-healthy/15 text-state-healthy hover:bg-state-healthy/25"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() =>
                          requestConfirm({
                            title: `Reject ${block.block_type}?`,
                            description: 'Rejecting a quarantined core block marks it deprecated and keeps an audit trail.',
                            confirmLabel: 'Reject Block',
                            variant: 'destructive',
                            onConfirm: () => rejectQuarantinedCoreBlock(block.block_type, operatorName, 'Rejected from War Room'),
                          })
                        }
                        className="px-3 py-1 text-xs rounded bg-state-error/15 text-state-error hover:bg-state-error/25"
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                  <p className="mt-2 text-xs text-text-secondary whitespace-pre-wrap break-words">{block.content}</p>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-4">
            Quarantined Tiered Items ({quarantinedItems.length})
          </h3>
          {quarantinedItems.length === 0 ? (
            <p className="text-sm text-text-muted">No quarantined tiered items.</p>
          ) : (
            <div className="space-y-3">
              {quarantinedItems.map((item: QuarantineItem) => {
                const review = quarantineReviewSummary(item)
                return (
                  <div key={item.id} className="rounded-md border border-border-default bg-surface-card-elevated p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={`text-xs font-mono ${tierTone(item.tier)}`}>{item.tier}</span>
                          <span className="text-sm text-text-primary truncate">{item.title}</span>
                          <span className={`rounded border px-2 py-0.5 text-[10px] font-medium ${quarantineBadgeClass(review.tone)}`}>
                            {review.label}
                          </span>
                        </div>
                        <div className="text-[10px] text-text-muted mt-1 font-mono">{item.id}</div>
                        <p className="mt-2 text-xs text-text-muted">{review.description}</p>
                      </div>
                      <div className="flex shrink-0 gap-2">
                        <button
                          onClick={() =>
                            runAction(
                              () => approveQuarantinedItem(item.tier, item.id, operatorName, 'Approved from War Room'),
                              `Approved quarantined item ${item.title}.`,
                            )
                          }
                          className="px-3 py-1 text-xs rounded bg-state-healthy/15 text-state-healthy hover:bg-state-healthy/25"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() =>
                            requestConfirm({
                              title: `Reject ${item.title}?`,
                              description: 'Rejecting a quarantined item deletes it from the selected tier.',
                              confirmLabel: 'Reject Item',
                              variant: 'destructive',
                              onConfirm: () => rejectQuarantinedItem(item.tier, item.id, operatorName, 'Rejected from War Room'),
                            })
                          }
                          className="px-3 py-1 text-xs rounded bg-state-error/15 text-state-error hover:bg-state-error/25"
                        >
                          Reject
                        </button>
                      </div>
                    </div>
                    {review.details.length > 0 ? (
                      <div className="mt-3 rounded border border-border-default bg-surface-input px-3 py-2">
                        {review.details.map((detail) => (
                          <div key={detail} className="text-[10px] text-text-secondary">
                            {detail}
                          </div>
                        ))}
                      </div>
                    ) : null}
                    <p className="mt-2 text-xs text-text-secondary whitespace-pre-wrap break-words">{item.content}</p>
                  </div>
                )
              })}
            </div>
          )}
        </section>
      </div>

      <section className="bg-surface-card border border-border-default rounded-lg p-4">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-4">
          Recent Tiered Memory Actions
        </h3>
        {recentItems.length === 0 ? (
          <p className="text-sm text-text-muted">No recent working, episodic, or archival items are available.</p>
        ) : (
          <div className="space-y-3">
            {recentItems.map((item: RecentMemoryItem) => (
              <div key={item.id} className="rounded-md border border-border-default bg-surface-card-elevated p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-xs font-mono ${tierTone(item.tier)}`}>{item.tier}</span>
                      <span className="text-sm font-medium text-text-primary truncate">{item.title}</span>
                      <span className="text-[10px] text-text-muted font-mono">{item.token_count} tokens</span>
                    </div>
                    <div className="text-[10px] text-text-muted mt-1">
                      {item.namespace} • {formatTimestamp(item.updated_at)} • {(item.confidence * 100).toFixed(0)}%
                    </div>
                    <p className="mt-2 text-xs text-text-secondary whitespace-pre-wrap break-words">{item.content}</p>
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <button
                      onClick={() =>
                        runAction(
                          () => updateMemoryItemStatus(item.tier, item.id, 'deprecated', operatorName, 'Archived from War Room'),
                          `Archived ${item.title}.`,
                        )
                      }
                      className="px-3 py-1 text-xs rounded border border-border-default text-text-secondary hover:border-border-active hover:text-text-primary"
                    >
                      Archive
                    </button>
                    <button
                      onClick={() =>
                        requestConfirm({
                          title: `Delete ${item.title}?`,
                          description: 'This permanently removes the memory item from storage.',
                          confirmLabel: 'Delete Item',
                          variant: 'destructive',
                          onConfirm: () => deleteMemoryItem(item.tier, item.id, operatorName, 'Deleted from War Room'),
                        })
                      }
                      className="px-3 py-1 text-xs rounded bg-state-error/15 text-state-error hover:bg-state-error/25"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="bg-surface-card border border-border-default rounded-lg p-4">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-4">
          Recent Commit History
        </h3>
        {recentCommits.length === 0 ? (
          <p className="text-sm text-text-muted">No committed governed memory changes have been recorded yet.</p>
        ) : (
          <div className="space-y-3">
            {recentCommits.map((commit: MemoryCommitSummary) => (
              <div key={commit.commit_id} className="rounded-md border border-border-default bg-surface-card-elevated p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-text-muted">{commit.status}</span>
                      <span className="text-sm font-medium text-text-primary truncate">
                        {commit.message || `Commit ${commit.commit_id}`}
                      </span>
                    </div>
                    <div className="text-[10px] text-text-muted mt-1">
                      {commit.created_by} • {formatTimestamp(commit.created_at)} • {commit.edit_count} edit{commit.edit_count === 1 ? '' : 's'}
                    </div>
                    <div className="mt-2 text-[10px] font-mono text-text-muted break-words">
                      {commit.affected_targets.join(', ') || 'No targets recorded'}
                    </div>
                    {commit.rollback_of ? (
                      <div className="mt-1 text-[10px] text-state-warning">
                        rollback of {commit.rollback_of}
                      </div>
                    ) : null}
                  </div>
                  {!commit.rollback_of ? (
                    <button
                      onClick={() =>
                        requestConfirm({
                          title: `Rollback commit ${commit.commit_id}?`,
                          description: 'This creates a new rollback commit and restores the previous core snapshot.',
                          confirmLabel: 'Rollback Commit',
                          variant: 'destructive',
                          onConfirm: () => rollbackCommit(commit.commit_id, operatorName, 'Rolled back from War Room'),
                        })
                      }
                      className="px-3 py-1 text-xs rounded border border-border-default text-text-secondary hover:border-border-active hover:text-text-primary"
                    >
                      Rollback
                    </button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={confirmState != null}
        title={confirmState?.title ?? ''}
        description={confirmState?.description ?? ''}
        confirmLabel={confirmState?.confirmLabel ?? 'Confirm'}
        variant={confirmState?.variant ?? 'default'}
        onCancel={() => setConfirmState(null)}
        onConfirm={() => {
          void handleConfirm()
        }}
      />
    </div>
  )
}
