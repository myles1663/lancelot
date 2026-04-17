import { apiGet, apiPost } from './client'
import type {
  CoreBlocksResponse,
  CoreBlock,
  MemorySearchResponse,
  RecentMemoryResponse,
  BeginCommitResponse,
  AddEditResponse,
  FinishCommitResponse,
  RollbackResponse,
  MemoryCommitHistoryResponse,
  QuarantineResponse,
  MemoryActionResponse,
  CompileContextResponse,
  MemoryStatsResponse,
} from '@/types/api'

/** GET /memory/core — All core blocks */
export function fetchCoreBlocks() {
  return apiGet<CoreBlocksResponse>('/memory/core')
}

/** GET /memory/core/:blockType — Single core block */
export function fetchCoreBlock(blockType: string) {
  return apiGet<CoreBlock>(`/memory/core/${blockType}`)
}

/** POST /memory/search — Search memory items */
export function searchMemory(query: string, limit = 20) {
  return apiPost<MemorySearchResponse>('/memory/search', { query, limit })
}

/** GET /memory/recent — Recent memory items across tiers */
export function fetchRecentMemory(limit = 12) {
  return apiGet<RecentMemoryResponse>(`/memory/recent?limit=${limit}`)
}

/** POST /memory/commit/begin — Start a staged commit */
export function beginCommit(_createdBy: string, message = '') {
  return apiPost<BeginCommitResponse>('/memory/commit/begin', {
    message,
  })
}

/** POST /memory/commit/:id/edit — Add an edit to a staged commit */
export function addEdit(commitId: string, edit: Record<string, unknown>) {
  return apiPost<AddEditResponse>(`/memory/commit/${commitId}/edit`, edit)
}

/** POST /memory/commit/:id/finish — Finalize a staged commit */
export function finishCommit(commitId: string) {
  return apiPost<FinishCommitResponse>(`/memory/commit/${commitId}/finish`)
}

/** GET /memory/commits — Recent governed memory commit history */
export function fetchMemoryCommits(limit = 25) {
  return apiGet<MemoryCommitHistoryResponse>(`/memory/commits?limit=${limit}`)
}

/** POST /memory/rollback/:id — Roll back a commit */
export function rollbackCommit(commitId: string, _createdBy: string, reason: string) {
  return apiPost<RollbackResponse>(`/memory/rollback/${commitId}`, {
    reason,
  })
}

/** GET /memory/quarantine — Quarantined blocks and items */
export function fetchQuarantine() {
  return apiGet<QuarantineResponse>('/memory/quarantine')
}

/** POST /memory/promote/:id — Promote a quarantined item */
export function promoteItem(itemId: string) {
  return apiPost<{ status: string; item_id: string }>(`/memory/promote/${itemId}`)
}

/** POST /memory/quarantine/:tier/:id/approve — Approve a quarantined item */
export function approveQuarantinedItem(tier: string, itemId: string, _operator: string, reason = '') {
  return apiPost<MemoryActionResponse>(`/memory/quarantine/${tier}/${itemId}/approve`, { reason })
}

/** POST /memory/quarantine/:tier/:id/reject — Reject a quarantined item */
export function rejectQuarantinedItem(tier: string, itemId: string, _operator: string, reason = '') {
  return apiPost<MemoryActionResponse>(`/memory/quarantine/${tier}/${itemId}/reject`, { reason })
}

/** POST /memory/quarantine/core/:block/approve — Approve a quarantined core block */
export function approveQuarantinedCoreBlock(blockType: string, _operator: string, reason = '') {
  return apiPost<MemoryActionResponse>(`/memory/quarantine/core/${blockType}/approve`, { reason })
}

/** POST /memory/quarantine/core/:block/reject — Reject a quarantined core block */
export function rejectQuarantinedCoreBlock(blockType: string, _operator: string, reason = '') {
  return apiPost<MemoryActionResponse>(`/memory/quarantine/core/${blockType}/reject`, { reason })
}

/** POST /memory/item/:tier/:id/status — Update tiered memory item lifecycle status */
export function updateMemoryItemStatus(tier: string, itemId: string, status: string, _operator: string, reason = '') {
  return apiPost<MemoryActionResponse>(`/memory/item/${tier}/${itemId}/status?status=${encodeURIComponent(status)}`, {
    reason,
  })
}

/** POST /memory/item/:tier/:id/delete — Permanently delete a tiered memory item */
export function deleteMemoryItem(tier: string, itemId: string, _operator: string, reason = '') {
  return apiPost<MemoryActionResponse>(`/memory/item/${tier}/${itemId}/delete`, { reason })
}

/** POST /memory/compile — Compile context for a conversation */
export function compileContext(params: Record<string, unknown> = {}) {
  return apiPost<CompileContextResponse>('/memory/compile', params)
}

/** GET /memory/stats — Memory subsystem statistics */
export function fetchMemoryStats() {
  return apiGet<MemoryStatsResponse>('/memory/stats')
}
