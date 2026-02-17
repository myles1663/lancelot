import { apiGet, apiPost } from './client'
import type { SkillProposalsResponse, SkillProposalDetail, SkillsListResponse } from '@/types/api'

/** GET /api/skills/proposals — list all skill proposals */
export function fetchSkillProposals() {
  return apiGet<SkillProposalsResponse>('/api/skills/proposals')
}

/** GET /api/skills/proposals/:id — get proposal detail with code */
export function fetchSkillProposal(proposalId: string) {
  return apiGet<SkillProposalDetail>(`/api/skills/proposals/${proposalId}`)
}

/** POST /api/skills/proposals/:id/approve — approve a pending proposal */
export function approveSkillProposal(proposalId: string) {
  return apiPost<{ status: string; proposal_id: string; name: string; approved_by: string }>(
    `/api/skills/proposals/${proposalId}/approve`,
    { approved_by: 'owner' },
  )
}

/** POST /api/skills/proposals/:id/reject — reject a pending proposal */
export function rejectSkillProposal(proposalId: string) {
  return apiPost<{ status: string; proposal_id: string; name: string }>(
    `/api/skills/proposals/${proposalId}/reject`,
  )
}

/** POST /api/skills/proposals/:id/install — install an approved proposal */
export function installSkillProposal(proposalId: string) {
  return apiPost<{ status: string; proposal_id: string; name: string; message: string }>(
    `/api/skills/proposals/${proposalId}/install`,
  )
}

/** GET /api/skills — list all installed skills */
export function fetchInstalledSkills() {
  return apiGet<SkillsListResponse>('/api/skills')
}
