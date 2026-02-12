import { apiGet, apiPost, apiPostForm } from './client'
import type { ChatResponse, ChatUploadResponse, CrusaderStatusResponse, CrusaderActionResponse } from '@/types/api'

/** POST /chat — Send a text message */
export function sendMessage(text: string, user = 'Commander') {
  return apiPost<ChatResponse>('/chat', { text, user })
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
  return apiPostForm<ChatUploadResponse>('/chat/upload', form)
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
