import { apiGet } from './client'
import type {
  HealthCheckResponse,
  HealthReadyResponse,
} from '@/types/api'

/** GET /health — Gateway health with component status */
export function fetchHealth() {
  return apiGet<HealthCheckResponse>('/health')
}

/** GET /health/ready — Full health snapshot with degraded reasons */
export function fetchHealthReady() {
  return apiGet<HealthReadyResponse>('/health/ready')
}
