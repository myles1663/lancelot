import { apiGet } from './client'
import type { SystemStatusResponse } from '@/types/api'

/** GET /system/status — Full system provisioning status */
export function fetchSystemStatus() {
  return apiGet<SystemStatusResponse>('/system/status')
}
