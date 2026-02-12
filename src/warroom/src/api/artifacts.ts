import { apiGet, apiPost } from './client'
import type {
  ArtifactsListResponse,
  ArtifactGetResponse,
  ArtifactStoreResponse,
  WarRoomArtifact,
} from '@/types/api'

/** GET /warroom/artifacts — List artifacts, optionally filtered by session */
export function fetchArtifacts(sessionId?: string) {
  return apiGet<ArtifactsListResponse>(
    '/warroom/artifacts',
    sessionId ? { session_id: sessionId } : undefined,
  )
}

/** GET /warroom/artifacts/:id — Get a single artifact */
export function fetchArtifact(artifactId: string) {
  return apiGet<ArtifactGetResponse>(`/warroom/artifacts/${artifactId}`)
}

/** POST /warroom/artifacts — Store a new artifact */
export function storeArtifact(data: Partial<WarRoomArtifact>) {
  return apiPost<ArtifactStoreResponse>('/warroom/artifacts', data)
}
