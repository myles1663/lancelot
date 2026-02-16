import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { usePolling } from '@/hooks'
import { fetchHealth, fetchHealthReady, fetchSoulStatus } from '@/api'
import { ProgressBar } from '@/components'
import type { HealthCheckResponse, HealthReadyResponse, SoulStatusResponse } from '@/types/api'

const POLL_INTERVAL = 5000

function stateColor(val: number, thresholds: [number, number] = [50, 90]) {
  if (val >= thresholds[1]) return 'text-state-healthy'
  if (val >= thresholds[0]) return 'text-state-degraded'
  return 'text-state-error'
}

function barColor(val: number, thresholds: [number, number] = [50, 90]) {
  if (val >= thresholds[1]) return 'bg-state-healthy'
  if (val >= thresholds[0]) return 'bg-state-degraded'
  return 'bg-state-error'
}

function connectionState(health: HealthCheckResponse | null, ready: HealthReadyResponse | null) {
  if (!health && !ready) return { label: 'INITIALIZING', color: 'text-state-inactive', pulse: true }
  const components = health?.components ?? {}
  const allOk = Object.values(components).every((v) => v === 'ok')
  const anyDegraded = Object.values(components).some((v) => v === 'degraded')
  if (allOk) return { label: 'ACTIVE', color: 'text-state-healthy', pulse: false }
  if (anyDegraded) return { label: 'DEGRADED', color: 'text-state-degraded', pulse: false }
  return { label: 'SEVERED', color: 'text-state-error', pulse: false }
}

function defensePosture(health: HealthCheckResponse | null) {
  if (!health) return { label: 'UNKNOWN', color: 'text-state-inactive' }
  if (health.crusader_mode) return { label: 'CRUSADER', color: 'text-accent-secondary' }
  return { label: 'NORMAL', color: 'text-state-healthy' }
}

interface VitalProps {
  label: string
  children: React.ReactNode
  tooltip?: string
}

function Vital({ label, children, tooltip }: VitalProps) {
  return (
    <div className="flex flex-col min-w-[120px] group relative" title={tooltip}>
      <span className="text-[10px] uppercase tracking-wider text-text-muted mb-0.5">{label}</span>
      {children}
    </div>
  )
}

interface ArmorPopoverProps {
  armorPct: number
  degradedReasons: string[]
}

function ArmorPopover({ armorPct, degradedReasons }: ArmorPopoverProps) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>()

  const handleMouseEnter = () => {
    clearTimeout(timeoutRef.current)
    setOpen(true)
  }

  const handleMouseLeave = () => {
    timeoutRef.current = setTimeout(() => setOpen(false), 150)
  }

  useEffect(() => {
    return () => clearTimeout(timeoutRef.current)
  }, [])

  return (
    <div
      className="flex flex-col min-w-[120px] group relative cursor-pointer"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onClick={() => navigate('/health')}
    >
      <span className="text-[10px] uppercase tracking-wider text-text-muted mb-0.5">
        Armor
      </span>
      <span className={`text-xs font-semibold font-mono ${stateColor(armorPct, [70, 90])}`}>
        {armorPct}%
      </span>
      <ProgressBar value={armorPct} color={barColor(armorPct, [70, 90])} className="mt-1" />

      {open && (
        <div
          className="absolute top-full left-0 mt-2 w-64 bg-surface-card border border-border-default rounded-lg shadow-lg p-3 z-50"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          <p className="text-[10px] uppercase tracking-wider text-text-muted mb-2">
            System Health
          </p>
          {degradedReasons.length === 0 ? (
            <p className="text-xs text-state-healthy">All systems operational</p>
          ) : (
            <ul className="space-y-1.5">
              {degradedReasons.map((reason, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-state-degraded mt-1.5 flex-shrink-0" />
                  <span className="text-xs text-text-secondary">{reason}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="text-[10px] text-text-muted mt-2 pt-2 border-t border-border-default">
            Click to open Health Dashboard
          </p>
        </div>
      )}
    </div>
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

  // Identity — based on soul status (100% if soul loaded, 0% if not)
  const identityPct = soul?.active_version ? 100 : 0
  const identityLabel = identityPct === 100 ? 'BONDED' : 'UNBONDED'

  // Armor — based on health readiness
  const armorPct = ready
    ? ready.degraded_reasons.length === 0
      ? 100
      : Math.max(0, 100 - ready.degraded_reasons.length * 20)
    : 0

  const conn = connectionState(health, ready)
  const defense = defensePosture(health)
  const isCrusader = health?.crusader_mode ?? false

  return (
    <div
      className={`flex items-center gap-6 flex-1 min-w-0 ${
        isCrusader ? 'ring-1 ring-accent-secondary/60 rounded px-2 animate-pulse' : ''
      }`}
    >
      {/* Identity Bonded */}
      <Vital
        label="Identity"
        tooltip="Soul contract integrity. 100% = all identity assertions verified."
      >
        <span className={`text-xs font-semibold font-mono ${stateColor(identityPct)}`}>
          {identityLabel} {identityPct}%
        </span>
        <ProgressBar value={identityPct} color={barColor(identityPct)} className="mt-1" />
      </Vital>

      {/* Armor Integrity — hover for details, click for Health page */}
      <ArmorPopover
        armorPct={armorPct}
        degradedReasons={ready?.degraded_reasons ?? []}
      />

      {/* Connection */}
      <Vital
        label="Connection"
        tooltip="Connection to LLM providers. ACTIVE = all providers responding."
      >
        <div className="flex items-center gap-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full ${conn.color.replace('text-', 'bg-')} ${
              conn.pulse ? 'animate-pulse' : ''
            }`}
          />
          <span className={`text-xs font-semibold font-mono ${conn.color}`}>{conn.label}</span>
        </div>
      </Vital>

      {/* Defense Posture */}
      <Vital
        label="Defense"
        tooltip="Current security posture. ELEVATED = anomalous activity. LOCKDOWN = safety trigger."
      >
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${defense.color.replace('text-', 'bg-')}`} />
          <span className={`text-xs font-semibold font-mono ${defense.color}`}>
            {defense.label}
          </span>
        </div>
      </Vital>

      {/* Crusader badge */}
      {isCrusader && (
        <span className="px-2 py-0.5 text-[10px] font-bold tracking-wider rounded bg-accent-secondary/20 text-accent-secondary">
          CRUSADER
        </span>
      )}
    </div>
  )
}
