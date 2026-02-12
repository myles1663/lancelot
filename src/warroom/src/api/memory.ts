import { apiGet, apiPost } from './client'
import type {
  CoreBlocksResponse,
  CoreBlock,
  MemorySearchResponse,
  BeginCommitResponse,
  AddEditResponse,
  FinishCommitResponse,
  RollbackResponse,
  QuarantineResponse,
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

/** POST /memory/commit/begin — Start a staged commit */
export function beginCommit(reason?: string) {
  return apiPost<BeginCommitResponse>('/memory/commit/begin', { reason })
}

/** POST /memory/commit/:id/edit — Add an edit to a staged commit */
export function addEdit(commitId: string, edit: Record<string, unknown>) {
  return apiPost<AddEditResponse>(`/memory/commit/${commitId}/edit`, edit)
}

/** POST /memory/commit/:id/finish — Finalize a staged commit */
export function finishCommit(commitId: string) {
  return apiPost<FinishCommitResponse>(`/memory/commit/${commitId}/finish`)
}

/** POST /memory/rollback/:id — Roll back a commit */
export function rollbackCommit(commitId: string) {
  return apiPost<RollbackResponse>(`/memory/rollback/${commitId}`)
}

/** GET /memory/quarantine — Quarantined blocks and items */
export function fetchQuarantine() {
  return apiGet<QuarantineResponse>('/memory/quarantine')
}

/** POST /memory/promote/:id — Promote a quarantined item */
export function promoteItem(itemId: string) {
  return apiPost<{ status: string; item_id: string }>(`/memory/promote/${itemId}`)
}

/** POST /memory/compile — Compile context for a conversation */
export function compileContext(params: Record<string, unknown> = {}) {
  return apiPost<CompileContextResponse>('/memory/compile', params)
}

/** GET /memory/stats — Memory subsystem statistics */
export function fetchMemoryStats() {
  return apiGet<MemoryStatsResponse>('/memory/stats')
}
