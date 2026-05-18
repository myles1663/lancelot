import { useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  HeartPulse,
  Shield,
  ShieldCheck,
  Wifi,
  type LucideIcon,
} from 'lucide-react'
import { usePolling } from '@/hooks'
import { fetchHealth, fetchHealthReady, fetchSoulStatus } from '@/api'
import type { HealthCheckResponse, HealthReadyResponse, SoulStatusResponse } from '@/types/api'

const POLL_INTERVAL = 5000

function stateColor(val: number, thresholds: [number, number] = [50, 90]) {
  if (val >= thresholds[1]) return 'text-state-healthy'
  if (val >= thresholds[0]) return 'text-state-degraded'
  return 'text-state-error'
}

function connectionState(health: HealthCheckResponse | null, ready: HealthReadyResponse | null) {
  if (!health && !ready) return { label: 'INITIALIZING', color: 'text-state-inactive', pulse: true }
  if (ready?.ready) return { label: 'ACTIVE', color: 'text-state-healthy', pulse: false }

  const components = health?.components ?? {}
  const coreComponents = ['gateway', 'orchestrator', 'local_llm']
  const coreValues = coreComponents
    .map((key) => components[key])
    .filter((value): value is string => typeof value === 'string' && value.length > 0)
  const gatewayOffline = health?.status && !['online', 'ok', 'healthy'].includes(health.status)
  const coreBroken = coreValues.some((v) => !['ok', 'disabled', 'degraded'].includes(v))
  const coreDegraded = coreValues.some((v) => v === 'degraded') || ready?.ready === false

  if (!gatewayOffline && !coreBroken && !coreDegraded) {
    return { label: 'ACTIVE', color: 'text-state-healthy', pulse: false }
  }
  if (!gatewayOffline && !coreBroken) {
    return { label: 'DEGRADED', color: 'text-state-degraded', pulse: false }
  }
  return { label: 'SEVERED', color: 'text-state-error', pulse: false }
}

function defensePosture(health: HealthCheckResponse | null) {
  if (!health) return { label: 'UNKNOWN', color: 'text-state-inactive' }
  if (health.crusader_mode) return { label: 'CRUSADER', color: 'text-accent-secondary' }
  return { label: 'NORMAL', color: 'text-state-healthy' }
}

interface VitalProps {
  label: string
  value: string
  color: string
  icon: LucideIcon
  tooltip?: string
  className?: string
}

function Vital({ label, value, color, icon: Icon, tooltip, className = '' }: VitalProps) {
  return (
    <div
      className={`flex h-8 min-w-0 items-center gap-2 rounded-md border border-border-default bg-surface-bg/55 px-2.5 ${className}`}
      title={tooltip}
    >
      <Icon className={`h-3.5 w-3.5 shrink-0 ${color}`} strokeWidth={1.9} aria-hidden="true" />
      <span className="hidden shrink-0 whitespace-nowrap text-[10px] font-semibold uppercase tracking-[0.14em] text-text-muted 2xl:inline">
        {label}
      </span>
      <span className={`min-w-0 truncate whitespace-nowrap font-mono text-xs font-semibold ${color}`}>{value}</span>
    </div>
  )
}

interface HealthMonitorProps {
  armorPct: number
}

function HealthMonitor({ armorPct }: HealthMonitorProps) {
  const navigate = useNavigate()

  const openHealthDashboard = () => {
    navigate('/health')
  }

  return (
    <button
      type="button"
      className="flex h-8 min-w-[74px] cursor-pointer items-center gap-2 rounded-md border border-border-default bg-surface-bg/55 px-2.5 transition-colors hover:border-border-active hover:bg-surface-card-elevated"
      onClick={openHealthDashboard}
      title="Open Health Dashboard"
      aria-label={`Open Health Dashboard. Health ${armorPct} percent.`}
    >
      <HeartPulse className={`h-3.5 w-3.5 shrink-0 ${stateColor(armorPct, [70, 90])}`} strokeWidth={1.9} aria-hidden="true" />
      <span className="hidden shrink-0 whitespace-nowrap text-[10px] font-semibold uppercase tracking-[0.14em] text-text-muted 2xl:inline">
        Health
      </span>
      <span className={`whitespace-nowrap font-mono text-xs font-semibold ${stateColor(armorPct, [70, 90])}`}>
        {armorPct}%
      </span>
    </button>
  )
}

export function VitalsBar() {
  const { data: health } = usePolling<HealthCheckResponse>({
    fetcher: fetchHealth,
    interval: POLL_INTERVAL,
  })
  const { data: ready } = usePolling<HealthReadyResponse>({
    fetcher: fetchHealthReady,
    interval: POLL_INTERVAL,
  })
  const { data: soul } = usePolling<SoulStatusResponse>({
    fetcher: fetchSoulStatus,
    interval: POLL_INTERVAL,
  })

  const identityPct = soul?.active_version ? 100 : 0
  const identityLabel = identityPct === 100 ? 'BONDED' : 'UNBONDED'

  const armorPct = ready
    ? ready.degraded_reasons.length === 0
      ? 100
      : Math.max(0, 100 - ready.degraded_reasons.length * 20)
    : 0

  const conn = connectionState(health, ready)
  const defense = defensePosture(health)
  const isCrusader = health?.crusader_mode ?? false
  const errorRate = health?.error_rate ?? 0

  return (
    <div
      className={`flex min-w-0 flex-1 items-center gap-2 overflow-hidden ${
        isCrusader ? 'rounded-md ring-1 ring-accent-secondary/60 animate-pulse' : ''
      }`}
    >
      <Vital
        label="Identity"
        value={`${identityLabel} ${identityPct}%`}
        color={stateColor(identityPct)}
        icon={ShieldCheck}
        tooltip="Soul contract integrity. 100% = all identity assertions verified."
        className="hidden w-[132px] md:flex xl:w-[138px]"
      />

      <HealthMonitor armorPct={armorPct} />

      <Vital
        label="Connection"
        value={conn.label}
        color={conn.color}
        icon={Wifi}
        tooltip="Browser-to-gateway control connection. LLM provider status is shown in Cost Tracker."
        className={`w-[98px] ${conn.pulse ? 'animate-pulse' : ''}`}
      />

      <Vital
        label="Defense"
        value={defense.label}
        color={defense.color}
        icon={Shield}
        tooltip="Current security posture. ELEVATED = anomalous activity. LOCKDOWN = safety trigger."
        className="hidden w-[96px] xl:flex"
      />

      <Vital
        label="Error Rate"
        value={health ? `${health.error_rate}%` : '--'}
        color={
          errorRate > 5
            ? 'text-state-error'
            : errorRate > 1
              ? 'text-state-degraded'
              : 'text-state-healthy'
        }
        icon={AlertCircle}
        tooltip="Percentage of requests returning 5xx errors since last restart."
        className="hidden w-[76px] 2xl:flex"
      />

      {isCrusader && (
        <span className="rounded bg-accent-secondary/20 px-2 py-0.5 text-[10px] font-bold tracking-wider text-accent-secondary">
          CRUSADER
        </span>
      )}
    </div>
  )
}
