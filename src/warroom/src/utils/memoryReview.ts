import type { QuarantineItem } from '@/types/api'

type ReviewTone = 'error' | 'warning' | 'healthy' | 'accent' | 'muted'

type ReviewSummary = {
  label: string
  description: string
  tone: ReviewTone
  details: string[]
}

type InjectionDetection = {
  reason?: string
  matched?: string
}

type ClaimSupersession = {
  entity_normalized?: string
  attribute?: string
  new_value?: string
  superseded_count?: number
}

type PromotionDecision = {
  reason?: string
  target_tier?: string
  suggested_status?: string
}

function metadataRecord(item: QuarantineItem): Record<string, unknown> {
  return item.detection_metadata ?? {}
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function injectionDetails(metadata: Record<string, unknown>): string[] {
  const injection = asRecord(metadata.injection_detection) as InjectionDetection | null
  if (!injection) return []
  return [
    injection.reason ? `Reason: ${injection.reason}` : '',
    injection.matched ? `Matched: ${injection.matched}` : '',
  ].filter(Boolean)
}

function ethicsDetails(metadata: Record<string, unknown>): string[] {
  return [
    asString(metadata.ethics_rule) ? `Rule: ${metadata.ethics_rule}` : '',
    asString(metadata.ethics_reason) ? `Reason: ${metadata.ethics_reason}` : '',
    metadata.ethics_exclude_from_context === true ? 'Context: excluded until reviewed' : '',
  ].filter(Boolean) as string[]
}

function claimDetails(metadata: Record<string, unknown>): string[] {
  const claims = Array.isArray(metadata.superseded_claims) ? (metadata.superseded_claims as ClaimSupersession[]) : []
  return claims.slice(0, 2).map((claim) => {
    const subject = [claim.entity_normalized, claim.attribute].filter(Boolean).join(' / ')
    const count = claim.superseded_count ?? 0
    return `${subject || 'Claim'}: ${count} prior value${count === 1 ? '' : 's'} superseded${claim.new_value ? ` by ${claim.new_value}` : ''}`
  })
}

function promotionDetails(metadata: Record<string, unknown>): string[] {
  const decision = asRecord(metadata.promotion_decision) as PromotionDecision | null
  if (!decision) return []
  return [
    decision.target_tier ? `Target: ${decision.target_tier}` : '',
    decision.suggested_status ? `Suggested: ${decision.suggested_status}` : '',
    decision.reason ? `Reason: ${decision.reason}` : '',
  ].filter(Boolean)
}

export function quarantineReviewSummary(item: QuarantineItem): ReviewSummary {
  const metadata = metadataRecord(item)
  const reason = item.flagged_reason ?? ''

  if (reason === 'injection_detected') {
    return {
      label: 'Prompt Injection',
      description: 'Excluded from compiled context and awaiting review.',
      tone: 'error',
      details: injectionDetails(metadata),
    }
  }

  if (reason === 'memory_ethics') {
    return {
      label: 'Memory Ethics',
      description: 'Quarantined by deterministic memory ethics policy.',
      tone: 'warning',
      details: ethicsDetails(metadata),
    }
  }

  if (reason === 'claim_supersession') {
    return {
      label: 'Claim Review',
      description: 'Contains structured claim changes that affect prior memory.',
      tone: 'accent',
      details: claimDetails(metadata),
    }
  }

  if (reason === 'promotion_review') {
    return {
      label: 'Promotion Review',
      description: 'Candidate memory needs operator review before tier promotion.',
      tone: 'healthy',
      details: promotionDetails(metadata),
    }
  }

  return {
    label: 'Governance Review',
    description: 'Quarantined item awaiting an operator decision.',
    tone: 'muted',
    details: [...ethicsDetails(metadata), ...injectionDetails(metadata), ...claimDetails(metadata), ...promotionDetails(metadata)],
  }
}

export function quarantineBadgeClass(tone: ReviewTone): string {
  switch (tone) {
    case 'error':
      return 'border-state-error/40 bg-state-error/10 text-state-error'
    case 'warning':
      return 'border-state-warning/40 bg-state-warning/10 text-state-warning'
    case 'healthy':
      return 'border-state-healthy/40 bg-state-healthy/10 text-state-healthy'
    case 'accent':
      return 'border-accent-primary/40 bg-accent-primary/10 text-accent-primary'
    default:
      return 'border-border-default bg-surface-input text-text-secondary'
  }
}

