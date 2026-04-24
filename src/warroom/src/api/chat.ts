import { apiGet, apiPost, apiPostForm } from './client'
import type {
  ChatAsyncResponse,
  ChatResponse,
  ChatRunCancelResponse,
  ChatRunsResponse,
  ChatUploadResponse,
  CrusaderStatusResponse,
  CrusaderActionResponse,
} from '@/types/api'

const CHAT_REQUEST_TIMEOUT_MS = 300000

/** POST /chat — Send a text message */
export function sendMessage(text: string, user = 'Commander') {
  return apiPost<ChatResponse>('/chat', { text, user }, CHAT_REQUEST_TIMEOUT_MS)
}

/** POST /chat/async — Queue a text message for async governed execution */
export function sendMessageAsync(text: string, user = 'Commander') {
  return apiPost<ChatAsyncResponse>('/chat/async', { text, user }, 60000)
}

/** POST /api/chat/runs/{run_id}/cancel — Mark an async run cancelled */
export function cancelChatRun(runId: string, reason = 'Cancelled by operator from Command Center.') {
  return apiPost<ChatRunCancelResponse>(
    `/api/chat/runs/${encodeURIComponent(runId)}/cancel`,
    { reason },
  )
}

/** POST /api/chat/runs/{run_id}/retry — Replay a failed, cancelled, or blocked async run */
export function retryChatRun(runId: string) {
  return apiPost<ChatAsyncResponse>(`/api/chat/runs/${encodeURIComponent(runId)}/retry`)
}

/** GET /api/chat/runs — Reconcile recent persisted async runs */
export function fetchChatRuns(limit = 25) {
  return apiGet<ChatRunsResponse>('/api/chat/runs', { limit: String(limit) })
}

/** POST /chat/upload — Send a message with file attachments */
export function sendMessageWithFiles(
  text: string,
  files: File[],
  user = 'Commander',
  saveToWorkspace = false,
) {
  const form = new FormData()
  form.append('text', text)
  form.append('user', user)
  form.append('save_to_workspace', String(saveToWorkspace))
  files.forEach((f) => form.append('files', f))
  return apiPostForm<ChatUploadResponse>('/chat/upload', form, CHAT_REQUEST_TIMEOUT_MS)
}

// ── Chat History ────────────────────────────────────────────

export interface ChatHistoryMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

export interface ChatHistoryResponse {
  messages: ChatHistoryMessage[]
  total: number
}

/** GET /api/chat/history — Load conversation history */
export function fetchChatHistory(limit = 50) {
  return apiGet<ChatHistoryResponse>('/api/chat/history', { limit: String(limit) })
}

/** GET /crusader_status — Current Crusader Mode state */
export function fetchCrusaderStatus() {
  return apiGet<CrusaderStatusResponse>('/crusader_status')
}

/** POST /api/crusader/activate — Activate Crusader Mode with flag+soul changes */
export function activateCrusader() {
  return apiPost<CrusaderActionResponse>('/api/crusader/activate')
}

/** POST /api/crusader/deactivate — Deactivate Crusader Mode, restore state */
export function deactivateCrusader() {
  return apiPost<CrusaderActionResponse>('/api/crusader/deactivate')
}
