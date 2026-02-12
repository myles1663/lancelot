import { apiGet, apiPost } from './client'
import type { SoulStatusResponse, SoulProposalActionResponse } from '@/types/api'

/** GET /soul/status — Active version + pending proposals */
export function fetchSoulStatus() {
  return apiGet<SoulStatusResponse>('/soul/status')
}

/** POST /soul/proposals/:id/approve — Approve a pending proposal */
export function approveSoulProposal(proposalId: string) {
  return apiPost<SoulProposalActionResponse>(`/soul/proposals/${proposalId}/approve`)
}

/** POST /soul/proposals/:id/activate — Activate an approved proposal */
export function activateSoulProposal(proposalId: string) {
  return apiPost<SoulProposalActionResponse>(`/soul/proposals/${proposalId}/activate`)
}
