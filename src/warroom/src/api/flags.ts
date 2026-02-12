import { apiGet } from './client'

export interface FlagsResponse {
  flags: Record<string, boolean>
}

export const fetchFlags = () => apiGet<FlagsResponse>('/api/flags')
