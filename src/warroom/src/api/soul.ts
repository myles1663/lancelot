import { apiGet, apiPost } from './client'
import type {
  SoulStatusResponse,
  SoulContentResponse,
  SoulProposalActionResponse,
  SoulProposeResponse,
  SoulTemplateListResponse,
  SoulTemplateDetail,
  SoulTemplateApplyResponse,
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
export function proposeSoulAmendment(proposed_yaml: string) {
  return apiPost<SoulProposeResponse>('/soul/propose', { proposed_yaml })
}

/** POST /soul/proposals/:id/approve — Approve a pending proposal */
export function approveSoulProposal(proposalId: string) {
  return apiPost<SoulProposalActionResponse>(`/soul/proposals/${proposalId}/approve`)
}

/** POST /soul/proposals/:id/activate — Activate an approved proposal */
export function activateSoulProposal(proposalId: string) {
  return apiPost<SoulProposalActionResponse>(`/soul/proposals/${proposalId}/activate`)
}

/** GET /soul/templates — List available Soul templates */
export function fetchSoulTemplates(industry?: string) {
  const params = industry ? `?industry=${encodeURIComponent(industry)}` : ''
  return apiGet<SoulTemplateListResponse>(`/soul/templates${params}`)
}

/** GET /soul/templates/:name — Get template details + full YAML */
export function fetchSoulTemplateDetail(name: string) {
  return apiGet<SoulTemplateDetail>(`/soul/templates/${encodeURIComponent(name)}`)
}

/** POST /soul/templates/:name/apply — Apply template as a proposal */
export function applySoulTemplate(
  name: string,
  _operatorId: string,
  _sessionId?: string,
  customizations?: Record<string, unknown>,
) {
  return apiPost<SoulTemplateApplyResponse>(
    `/soul/templates/${encodeURIComponent(name)}/apply`,
    { customizations },
  )
}
