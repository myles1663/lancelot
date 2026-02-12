import { apiGet, apiPost } from './client'
import type {
  SoulStatusResponse,
  SoulContentResponse,
  SoulProposalActionResponse,
  SoulProposeResponse,
} from '@/types/api'

/** GET /soul/status — Active version + pending proposals */
export function fetchSoulStatus() {
  return apiGet<SoulStatusResponse>('/soul/status')
}

/** GET /soul/content — Full active soul document + raw YAML */
export function fetchSoulContent() {
  return apiGet<SoulContentResponse>('/soul/content')
}

/** POST /soul/propose — Create amendment proposal from edited YAML */
export function proposeSoulAmendment(proposed_yaml: string, author: string = 'Commander') {
  return apiPost<SoulProposeResponse>('/soul/propose', { proposed_yaml, author })
}

/** POST /soul/proposals/:id/approve — Approve a pending proposal */
export function approveSoulProposal(proposalId: string) {
  return apiPost<SoulProposalActionResponse>(`/soul/proposals/${proposalId}/approve`)
}

/** POST /soul/proposals/:id/activate — Activate an approved proposal */
export function activateSoulProposal(proposalId: string) {
  return apiPost<SoulProposalActionResponse>(`/soul/proposals/${proposalId}/activate`)
}
