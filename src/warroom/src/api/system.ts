import { apiGet, apiPost, apiPut } from './client'
import type {
  ModelUsagePolicyResponse,
  RuntimeEmergencyStopResponse,
  RuntimePauseStatusResponse,
  SystemStatusResponse,
} from '@/types/api'

/** GET /system/status — Full system provisioning status */
export function fetchSystemStatus() {
  return apiGet<SystemStatusResponse>('/system/status')
}

export function fetchRuntimePauseStatus() {
  return apiGet<RuntimePauseStatusResponse>('/system/pause')
}

export function pauseRuntime(reason: string) {
  return apiPost<RuntimePauseStatusResponse>('/system/pause', { reason })
}

export function resumeRuntime() {
  return apiPost<RuntimePauseStatusResponse>('/system/resume')
}

export function emergencyStopRuntime(reason: string) {
  return apiPost<RuntimeEmergencyStopResponse>('/system/emergency-stop', { reason })
}

export function fetchModelUsagePolicy() {
  return apiGet<ModelUsagePolicyResponse>('/system/model-policy')
}

export function updateModelUsagePolicy(payload: {
  local_execution_mode?: string
  frontier_scrub_mode?: string
}) {
  return apiPut<ModelUsagePolicyResponse>('/system/model-policy', payload)
}
