type RiskTier = 'T0' | 'T1' | 'T2' | 'T3'

const TIER_CONFIG: Record<RiskTier, { label: string; color: string; bg: string }> = {
  T0: { label: 'T0 INERT', color: 'text-tier-t0', bg: 'bg-tier-t0/15' },
  T1: { label: 'T1 REVERSIBLE', color: 'text-tier-t1', bg: 'bg-tier-t1/15' },
  T2: { label: 'T2 CONTROLLED', color: 'text-tier-t2', bg: 'bg-tier-t2/15' },
  T3: { label: 'T3 IRREVERSIBLE', color: 'text-tier-t3', bg: 'bg-tier-t3/15' },
}

const TIER_KEYS: RiskTier[] = ['T0', 'T1', 'T2', 'T3']

function toTierKey(tier: RiskTier | number): RiskTier {
  if (typeof tier === 'number') return TIER_KEYS[Math.min(Math.max(tier, 0), 3)] ?? 'T0'
  return tier
}

interface TierBadgeProps {
  tier: RiskTier | number
  compact?: boolean
  className?: string
}

export function TierBadge({ tier, compact = false, className = '' }: TierBadgeProps) {
  const key = toTierKey(tier)
  const config = TIER_CONFIG[key]
  return (
    <span
      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ${config.color} ${config.bg} ${className}`}
    >
      {compact ? key : config.label}
    </span>
  )
}

export type { RiskTier }
