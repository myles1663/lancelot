import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { usePolling, usePageTitle } from '@/hooks'
import { fetchSystemStatus, fetchFlags, toggleFlag, fetchCrusaderStatus } from '@/api'
import { fetchNetworkAllowlist, updateNetworkAllowlist, fetchHostAgentStatus, shutdownHostAgent, fetchHostWriteCommands, saveHostWriteCommands, fetchHostWriteStatus, toggleHostWriteCommands, fetchUABStatus, fetchUABApps } from '@/api/flags'
import type { HostAgentStatus, UABStatus, UABConnectedApp } from '@/api/flags'
import { ConfirmDialog } from '@/components'
import type { FlagInfo } from '@/api/flags'
import type { CrusaderStatusResponse } from '@/types/api'

// ── Constants ─────────────────────────────────────────────────────────

const CATEGORY_ORDER = ['Core Subsystem', 'Tool Fabric', 'Runtime', 'Governance', 'Capabilities', 'Intelligence', 'Reasoning', 'Other']

// Master switches: subsystem circuit breakers shown in always-expanded block
const MASTER_SWITCH_NAMES = new Set([
  'FEATURE_MCP',
  'FEATURE_A2A',
  'FEATURE_OBSERVABILITY',
  'FEATURE_INCIDENT_RESPONSE',
  'FEATURE_FEDERATION',
  'FEATURE_HIVE',
  'MCP_ALL',
  'A2A_ALL',
  'FEDERATION_KILL',
])

// Human-readable labels for master switches
const MASTER_LABELS: Record<string, string> = {
  'FEATURE_MCP': 'MCP (Model Context Protocol)',
  'FEATURE_A2A': 'A2A (Agent-to-Agent Protocol)',
  'FEATURE_OBSERVABILITY': 'Observability (OTel + Webhooks)',
  'FEATURE_INCIDENT_RESPONSE': 'Incident Response Engine',
  'FEATURE_FEDERATION': 'Federation (Multi-Instance)',
  'FEATURE_HIVE': 'HIVE Agent Mesh',
  'MCP_ALL': 'MCP (All Traffic — Runtime Kill)',
  'A2A_ALL': 'A2A (All Traffic — Runtime Kill)',
  'FEDERATION_KILL': 'Federation Emergency Stop',
}

// Governance-critical flags that trigger red badge when off
const GOVERNANCE_FLAGS = new Set([
  'FEATURE_SOUL',
  'FEATURE_RISK_TIERED_GOVERNANCE',
  'FEATURE_SKILL_SECURITY_PIPELINE',
  'FEATURE_TRUST_LEDGER',
])

type StatusFilter = 'all' | 'active' | 'inactive' | 'deps_unmet'

// ── Toggle Component ──────────────────────────────────────────────────

function Toggle({
  enabled,
  disabled,
  large,
  onClick,
  title,
}: {
  enabled: boolean
  disabled?: boolean
  large?: boolean
  onClick?: (e: React.MouseEvent) => void
  title?: string
}) {
  const w = large ? 'w-14 h-7' : 'w-11 h-6'
  const knob = large ? 'w-6 h-6' : 'w-5 h-5'
  const translate = large ? 'translate-x-7' : 'translate-x-5'
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`relative ${w} rounded-full transition-colors duration-200 flex-shrink-0 ${
        enabled ? 'bg-state-healthy' : 'bg-surface-input border border-border-default'
      } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
    >
      <span className={`absolute top-0.5 left-0.5 ${knob} rounded-full bg-white shadow transition-transform duration-200 ${
        enabled ? translate : 'translate-x-0'
      }`} />
    </button>
  )
}

// ── Layer 1: Status Header ────────────────────────────────────────────

function StatusHeader({
  totalCount,
  activeCount,
  depsUnmetCount,
  criticalOff,
}: {
  totalCount: number
  activeCount: number
  depsUnmetCount: number
  criticalOff: string[]
}) {
  const inactiveCount = totalCount - activeCount
  const allGood = inactiveCount === 0 && depsUnmetCount === 0 && criticalOff.length === 0

  return (
    <div className={`flex items-center justify-between p-3 rounded-lg border ${
      allGood
        ? 'bg-state-healthy/5 border-state-healthy/20'
        : criticalOff.length > 0
          ? 'bg-state-error/5 border-state-error/20'
          : 'bg-state-degraded/5 border-state-degraded/20'
    }`}>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${allGood ? 'bg-state-healthy' : criticalOff.length > 0 ? 'bg-state-error animate-pulse' : 'bg-state-degraded'}`} />
          <span className="text-sm font-medium text-text-primary">
            {totalCount} switches
          </span>
          <span className="text-sm text-text-muted">/</span>
          <span className={`text-sm font-medium ${allGood ? 'text-state-healthy' : 'text-text-primary'}`}>
            {activeCount} active
          </span>
        </div>

        {inactiveCount > 0 && (
          <span className="text-xs px-2 py-0.5 rounded bg-state-degraded/15 text-state-degraded font-medium">
            {inactiveCount} inactive
          </span>
        )}

        {depsUnmetCount > 0 && (
          <span className="text-xs px-2 py-0.5 rounded bg-state-error/15 text-state-error font-medium">
            {depsUnmetCount} deps unmet
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        {criticalOff.map(name => (
          <span key={name} className="text-[10px] px-2 py-0.5 rounded bg-state-error/15 text-state-error font-mono font-medium">
            {name.replace('FEATURE_', '')} OFF
          </span>
        ))}
        {criticalOff.length === 0 && allGood && (
          <span className="text-xs text-state-healthy font-medium">All systems nominal</span>
        )}
      </div>
    </div>
  )
}

// ── Layer 2: Search ───────────────────────────────────────────────────

function SearchBar({
  value,
  onChange,
  statusFilter,
  onStatusFilterChange,
  onClear,
}: {
  value: string
  onChange: (v: string) => void
  statusFilter: StatusFilter
  onStatusFilterChange: (f: StatusFilter) => void
  onClear: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === '/' && document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA') {
        e.preventDefault()
        inputRef.current?.focus()
      }
      if (e.key === 'Escape' && document.activeElement === inputRef.current) {
        onClear()
        inputRef.current?.blur()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClear])

  const filters: { id: StatusFilter; label: string }[] = [
    { id: 'all', label: 'All' },
    { id: 'active', label: 'Active' },
    { id: 'inactive', label: 'Inactive' },
    { id: 'deps_unmet', label: 'Deps Unmet' },
  ]

  return (
    <div className="flex items-center gap-2">
      <div className="relative flex-1">
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Search switches..."
          className="w-full bg-surface-input border border-border-default rounded-lg px-3 py-2 pr-16 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary"
        />
        {value ? (
          <button onClick={onClear} className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary text-sm px-1">
            &times;
          </button>
        ) : (
          <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-text-muted font-mono bg-surface-card-elevated px-1.5 py-0.5 rounded border border-border-default">
            /
          </span>
        )}
      </div>

      <div className="flex items-center gap-1">
        {filters.map(f => (
          <button
            key={f.id}
            onClick={() => onStatusFilterChange(f.id)}
            className={`px-2.5 py-1.5 text-xs font-medium rounded-lg transition-colors ${
              statusFilter === f.id
                ? 'bg-accent-primary/15 text-accent-primary border border-accent-primary/30'
                : 'bg-surface-input text-text-muted border border-transparent hover:text-text-primary hover:bg-surface-card-elevated'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Layer 3: Master Switch Block ──────────────────────────────────────

function MasterSwitchRow({
  name,
  info,
  flags,
  isCrusaderOverride,
  isToggling,
  onToggle,
}: {
  name: string
  info: FlagInfo
  flags: Record<string, FlagInfo>
  isCrusaderOverride: boolean
  isToggling: boolean
  onToggle: (e: React.MouseEvent, name: string, info: FlagInfo) => void
}) {
  // Count dependent switches in deps-unmet state
  const dependentCount = useMemo(() => {
    if (info.enabled) return 0
    let count = 0
    for (const [, fi] of Object.entries(flags)) {
      if (fi.requires.includes(name) && !fi.requires.every(dep => flags[dep]?.enabled)) {
        count++
      }
    }
    return count
  }, [name, info.enabled, flags])

  const label = MASTER_LABELS[name] || name.replace('FEATURE_', '').replace(/_/g, ' ')

  return (
    <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-lg">
      <div className="flex flex-col gap-0.5 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-text-primary">{label}</span>
          {isCrusaderOverride && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-secondary/15 text-accent-secondary">crusader</span>
          )}
          {info.restart_required && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-primary/15 text-accent-primary">restart</span>
          )}
        </div>
        <span className="text-[10px] font-mono text-text-muted">{name}</span>
        {!info.enabled && dependentCount > 0 && (
          <span className="text-[10px] text-state-degraded">
            {dependentCount} dependent switch{dependentCount !== 1 ? 'es' : ''} paused
          </span>
        )}
      </div>
      <Toggle
        enabled={info.enabled}
        disabled={isToggling || isCrusaderOverride}
        large
        title={isCrusaderOverride ? 'Locked by Crusader Mode' : undefined}
        onClick={(e) => onToggle(e, name, info)}
      />
    </div>
  )
}

// ── Layer 4: Category Accordion ───────────────────────────────────────

function CategoryAccordion({
  category,
  switches,
  flags,
  isOpen,
  onToggleOpen,
  isSearchActive,
  expanded,
  onToggleExpand,
  overriddenFlags,
  toggling,
  onToggle,
}: {
  category: string
  switches: [string, FlagInfo][]
  flags: Record<string, FlagInfo>
  isOpen: boolean
  onToggleOpen: () => void
  isSearchActive: boolean
  expanded: Set<string>
  onToggleExpand: (name: string) => void
  overriddenFlags: Set<string>
  toggling: string | null
  onToggle: (e: React.MouseEvent, name: string, info: FlagInfo) => void
}) {
  const shouldShow = isSearchActive || isOpen

  // Status dot: red if any deps unmet, amber if any inactive, green if all active
  const hasDepsUnmet = switches.some(([, info]) => info.requires.some(dep => !flags[dep]?.enabled))
  const hasInactive = switches.some(([, info]) => !info.enabled)
  const dotColor = hasDepsUnmet ? 'bg-state-error' : hasInactive ? 'bg-state-degraded' : 'bg-state-healthy'

  return (
    <div className="bg-surface-card border border-border-default rounded-lg overflow-hidden">
      {/* Header */}
      <button
        onClick={onToggleOpen}
        className="w-full flex items-center justify-between p-3 hover:bg-surface-card-elevated/50 transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"
            className={`text-text-muted transition-transform ${shouldShow ? 'rotate-90' : ''}`}>
            <path d="M4.5 2.5L8 6L4.5 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="text-sm font-medium text-text-primary">{category}</span>
          <span className="text-xs text-text-muted">{switches.length} switch{switches.length !== 1 ? 'es' : ''}</span>
          <span className={`w-2 h-2 rounded-full ${dotColor}`} />
        </div>
      </button>

      {/* Body */}
      {shouldShow && (
        <div className="border-t border-border-default px-3 pb-3 space-y-1">
          {switches.map(([name, info]) => (
            <FlagRow
              key={name}
              name={name}
              info={info}
              flags={flags}
              isExpanded={expanded.has(name)}
              onToggleExpand={() => onToggleExpand(name)}
              isCrusaderOverride={overriddenFlags.has(name)}
              isToggling={toggling === name}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Flag Row (reused in accordions) ───────────────────────────────────

function FlagRow({
  name,
  info,
  flags,
  isExpanded,
  onToggleExpand,
  isCrusaderOverride,
  isToggling,
  onToggle,
}: {
  name: string
  info: FlagInfo
  flags: Record<string, FlagInfo>
  isExpanded: boolean
  onToggleExpand: () => void
  isCrusaderOverride: boolean
  isToggling: boolean
  onToggle: (e: React.MouseEvent, name: string, info: FlagInfo) => void
}) {
  const unmet = info.requires.filter(dep => !flags[dep]?.enabled)
  const hasConflict = info.conflicts.some(c => flags[c]?.enabled)
  const isToggleDisabled = isToggling || isCrusaderOverride

  return (
    <div className={`bg-surface-card-elevated rounded-md border overflow-hidden mt-1 ${
      isCrusaderOverride ? 'border-accent-secondary/30' : 'border-border-default'
    }`}>
      {/* Row */}
      <div
        className="flex items-center justify-between p-3 cursor-pointer hover:bg-surface-input/50 transition-colors"
        onClick={onToggleExpand}
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className={`text-[10px] transition-transform ${isExpanded ? 'rotate-90' : ''}`}>&#9654;</span>
          <span className="text-xs font-mono text-text-primary truncate">{name}</span>
          {isCrusaderOverride && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-secondary/15 text-accent-secondary whitespace-nowrap">crusader</span>
          )}
          {info.restart_required && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-primary/15 text-accent-primary whitespace-nowrap">restart</span>
          )}
          {unmet.length > 0 && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-state-degraded/15 text-state-degraded whitespace-nowrap">deps unmet</span>
          )}
          {hasConflict && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-state-error/15 text-state-error whitespace-nowrap">conflict</span>
          )}
        </div>
        <Toggle
          enabled={info.enabled}
          disabled={isToggleDisabled}
          title={isCrusaderOverride ? 'Locked by Crusader Mode' : undefined}
          onClick={(e) => { e.stopPropagation(); if (!isCrusaderOverride) onToggle(e, name, info) }}
        />
      </div>

      {/* Expanded details */}
      {isExpanded && (
        <div className="px-3 pb-3 pt-0 border-t border-border-default/50 space-y-2">
          <p className="text-xs text-text-secondary leading-relaxed mt-2">{info.description}</p>

          {info.warning && (
            <div className="p-2 rounded bg-state-degraded/8 border border-state-degraded/20">
              <p className="text-[11px] text-state-degraded leading-relaxed">
                <span className="font-semibold">Warning:</span> {info.warning}
              </p>
            </div>
          )}

          {info.requires.length > 0 && (
            <div className="flex items-start gap-2">
              <span className="text-[10px] text-text-muted uppercase tracking-wider pt-0.5 whitespace-nowrap">Requires:</span>
              <div className="flex flex-wrap gap-1">
                {info.requires.map(dep => {
                  const met = flags[dep]?.enabled
                  return (
                    <span key={dep} className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                      met ? 'bg-state-healthy/15 text-state-healthy' : 'bg-state-error/15 text-state-error'
                    }`}>
                      {dep.replace('FEATURE_', '')} {met ? '\u2713' : '\u2717'}
                    </span>
                  )
                })}
              </div>
            </div>
          )}

          {info.conflicts.length > 0 && (
            <div className="flex items-start gap-2">
              <span className="text-[10px] text-text-muted uppercase tracking-wider pt-0.5 whitespace-nowrap">Conflicts:</span>
              <div className="flex flex-wrap gap-1">
                {info.conflicts.map(c => {
                  const active = flags[c]?.enabled
                  return (
                    <span key={c} className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                      active ? 'bg-state-error/15 text-state-error' : 'bg-surface-input text-text-muted'
                    }`}>
                      {c.replace('FEATURE_', '')} {active ? '(active!)' : '(off)'}
                    </span>
                  )
                })}
              </div>
            </div>
          )}

          {info.has_editor === 'network_allowlist' && <AllowlistEditor />}
          {info.has_editor === 'host_agent' && <HostAgentPanel />}
          {info.has_editor === 'uab_panel' && <UABPanel />}
        </div>
      )}
    </div>
  )
}

// ── Inline Allowlist Editor ─────────────────────────────────────────

function AllowlistEditor() {
  const [domains, setDomains] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fetchNetworkAllowlist()
      setDomains(res.domains)
      setDraft(res.domains.join('\n'))
      setLoaded(true)
    } catch {
      setError('Failed to load allowlist')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      const lines = draft.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#'))
      const res = await updateNetworkAllowlist(lines)
      setDomains(res.domains)
      setDraft(res.domains.join('\n'))
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch {
      setError('Failed to save allowlist')
    } finally {
      setSaving(false)
    }
  }

  const hasChanges = loaded && draft.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#')).sort().join(',') !== [...domains].sort().join(',')

  return (
    <div className="mt-2 p-3 bg-surface-card rounded-lg border border-border-default">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">Allowed Domains</span>
        <span className="text-[10px] text-text-muted">{domains.length} domain{domains.length !== 1 ? 's' : ''}</span>
      </div>
      <textarea value={draft} onChange={e => setDraft(e.target.value)}
        placeholder="api.github.com&#10;api.anthropic.com&#10;..." rows={6}
        className="w-full bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary resize-y" />
      <p className="text-[10px] text-text-muted mt-1 mb-2">One domain per line. Exact match only — no wildcards. Lines starting with # are ignored.</p>
      <div className="flex items-center gap-2">
        <button onClick={handleSave} disabled={saving || !hasChanges}
          className={`px-3 py-1 text-[11px] font-medium rounded transition-colors ${
            hasChanges ? 'bg-accent-primary text-white hover:bg-accent-primary/80' : 'bg-surface-input text-text-muted cursor-not-allowed'
          }`}>
          {saving ? 'Saving...' : 'Save Allowlist'}
        </button>
        {success && <span className="text-[10px] text-state-healthy">Saved</span>}
        {error && <span className="text-[10px] text-state-error">{error}</span>}
      </div>
    </div>
  )
}

// ── Inline Host Agent Panel ─────────────────────────────────────────

function HostAgentPanel() {
  const [status, setStatus] = useState<HostAgentStatus | null>(null)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [writeEnabled, setWriteEnabled] = useState(false)
  const [writeToggling, setWriteToggling] = useState(false)
  const [writeConfirm, setWriteConfirm] = useState(false)
  const inFlightRef = useRef(false)

  const poll = useCallback(async () => {
    if (inFlightRef.current) return

    inFlightRef.current = true
    try {
      const [agentRes, writeRes] = await Promise.all([fetchHostAgentStatus(), fetchHostWriteStatus()])
      setStatus(agentRes)
      setWriteEnabled(writeRes.enabled)
      setError(null)
    } catch {
      setError('Failed to check agent status')
    } finally {
      inFlightRef.current = false
    }
  }, [])

  useEffect(() => { poll(); const t = setInterval(poll, 5000); return () => clearInterval(t) }, [poll])

  const handleStop = async () => {
    setStopping(true)
    try { await shutdownHostAgent(); setTimeout(poll, 2000) }
    catch { setError('Failed to stop agent') }
    finally { setStopping(false) }
  }

  const handleWriteToggle = async () => {
    if (!writeEnabled) { setWriteConfirm(true); return }
    setWriteToggling(true)
    try { const res = await toggleHostWriteCommands(); setWriteEnabled(res.enabled) }
    catch { setError('Failed to toggle write commands') }
    finally { setWriteToggling(false) }
  }

  const confirmWriteEnable = async () => {
    setWriteConfirm(false)
    setWriteToggling(true)
    try { const res = await toggleHostWriteCommands(); setWriteEnabled(res.enabled) }
    catch { setError('Failed to enable write commands') }
    finally { setWriteToggling(false) }
  }

  const reachable = status?.reachable ?? false

  return (
    <div className="mt-2 space-y-3">
      <div className="p-3 bg-surface-card rounded-lg border border-border-default">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">Host Agent</span>
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${reachable ? 'bg-state-healthy animate-pulse' : 'bg-state-error'}`} />
            <span className={`text-[10px] font-medium ${reachable ? 'text-state-healthy' : 'text-state-error'}`}>
              {reachable ? 'Running' : 'Offline'}
            </span>
          </div>
        </div>
        {reachable && status && (
          <div className="space-y-1.5 mb-3">
            <div className="flex items-center justify-between"><span className="text-[10px] text-text-muted">Platform</span><span className="text-[10px] font-mono text-text-primary">{status.platform} {status.platform_version}</span></div>
            <div className="flex items-center justify-between"><span className="text-[10px] text-text-muted">Hostname</span><span className="text-[10px] font-mono text-text-primary">{status.hostname}</span></div>
            <div className="flex items-center justify-between"><span className="text-[10px] text-text-muted">Agent Version</span><span className="text-[10px] font-mono text-text-primary">v{status.agent_version}</span></div>
          </div>
        )}
        {reachable ? (
          <button onClick={handleStop} disabled={stopping}
            className="px-3 py-1 text-[11px] font-medium rounded bg-state-error/15 text-state-error hover:bg-state-error/25 transition-colors disabled:opacity-50">
            {stopping ? 'Stopping...' : 'Stop Agent'}
          </button>
        ) : (
          <div className="space-y-2">
            <p className="text-[10px] text-text-muted leading-relaxed">The host agent is not running. To install it as a background service, run:</p>
            <code className="block text-[10px] font-mono bg-surface-input rounded px-2 py-1.5 text-text-primary select-all">host_agent\install_service.bat</code>
            <p className="text-[10px] text-text-muted">Or start manually: <code className="font-mono text-text-primary">host_agent\start_agent.bat</code></p>
          </div>
        )}
      </div>

      <div className={`p-3 rounded-lg border ${writeEnabled ? 'bg-red-500/5 border-red-500/30' : 'bg-surface-card border-border-default'}`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`text-[11px] font-medium uppercase tracking-wider ${writeEnabled ? 'text-red-400' : 'text-text-secondary'}`}>Write Commands</span>
            {writeEnabled && <span className="text-[9px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-400 font-bold">DANGER</span>}
          </div>
          <button onClick={handleWriteToggle} disabled={writeToggling}
            className={`relative w-11 h-6 rounded-full transition-colors duration-200 flex-shrink-0 ${
              writeEnabled ? 'bg-red-500' : 'bg-surface-input border border-border-default'
            } ${writeToggling ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}>
            <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform duration-200 ${writeEnabled ? 'translate-x-5' : 'translate-x-0'}`} />
          </button>
        </div>
        {!writeEnabled && <p className="text-[10px] text-text-muted mt-2">Destructive host commands disabled. Enable to allow rm, del, kill, etc.</p>}
        {writeEnabled && <HostWriteCommandsEditor />}
      </div>

      {writeConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-surface-card border-2 border-red-500/50 rounded-xl p-6 max-w-md mx-4 shadow-2xl">
            <h3 className="text-lg font-bold text-red-400 mb-3">EXTREME DANGER</h3>
            <p className="text-sm text-text-secondary leading-relaxed mb-2">This enables <span className="font-bold text-red-400">DESTRUCTIVE</span> commands (rm, del, kill, shutdown, etc.) on your <span className="font-bold text-red-400">REAL HOST MACHINE</span>.</p>
            <p className="text-sm text-text-secondary leading-relaxed mb-2">Files deleted <span className="font-bold text-red-400">CANNOT be recovered</span>. Services stopped may not restart. Registry edits can break your system.</p>
            <p className="text-sm text-text-secondary leading-relaxed mb-4">All write commands still require Sentry approval, but mistakes are <span className="font-bold text-red-400">IRREVERSIBLE</span>.</p>
            <div className="flex gap-3">
              <button onClick={() => setWriteConfirm(false)} className="flex-1 px-4 py-2 text-sm font-medium rounded-lg bg-surface-input text-text-primary hover:bg-surface-input/80 transition-colors">Cancel</button>
              <button onClick={confirmWriteEnable} className="flex-1 px-4 py-2 text-sm font-bold rounded-lg bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30 transition-colors">I Accept the Risk</button>
            </div>
          </div>
        </div>
      )}

      {error && <p className="text-[10px] text-state-error">{error}</p>}
    </div>
  )
}

// ── Host Write Commands Editor ──────────────────────────────────────

function HostWriteCommandsEditor() {
  const [draft, setDraft] = useState('')
  const [original, setOriginal] = useState('')
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const load = useCallback(async () => {
    try { const res = await fetchHostWriteCommands(); setDraft(res.raw); setOriginal(res.raw); setLoaded(true) }
    catch { setError('Failed to load write commands') }
  }, [])

  useEffect(() => { load() }, [load])

  const handleSave = async () => {
    setSaving(true); setError(null); setSuccess(false)
    try { await saveHostWriteCommands(draft); setOriginal(draft); setSuccess(true); setTimeout(() => setSuccess(false), 3000) }
    catch { setError('Failed to save write commands') }
    finally { setSaving(false) }
  }

  const hasChanges = loaded && draft !== original
  const cmdCount = draft.split('\n').filter(l => l.trim() && !l.trim().startsWith('#')).length

  return (
    <div className="mt-2 p-3 bg-surface-card rounded-lg border-2 border-red-500/40">
      <div className="mb-3 p-2.5 bg-red-500/10 border border-red-500/30 rounded-lg">
        <span className="text-red-400 text-sm font-bold">DANGER — HOST WRITE COMMANDS</span>
        <p className="text-[10px] text-red-300/80 leading-relaxed mt-1">
          These commands can <span className="font-bold text-red-400">PERMANENTLY DESTROY</span> data on your host machine.
          All executions still require Sentry approval, but mistakes are <span className="font-bold text-red-400">IRREVERSIBLE</span>.
        </p>
      </div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-medium text-red-400 uppercase tracking-wider">Allowed Write Commands</span>
        <span className="text-[10px] text-text-muted">{cmdCount} command{cmdCount !== 1 ? 's' : ''}</span>
      </div>
      <textarea value={draft} onChange={e => setDraft(e.target.value)} placeholder="rm&#10;del&#10;kill&#10;..." rows={8}
        className="w-full bg-surface-input border border-red-500/30 rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-red-500 resize-y" />
      <p className="text-[10px] text-text-muted mt-1 mb-2">One command binary per line. Lines starting with # are comments.</p>
      <div className="flex items-center gap-2">
        <button onClick={handleSave} disabled={saving || !hasChanges}
          className={`px-3 py-1 text-[11px] font-medium rounded transition-colors ${
            hasChanges ? 'bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30' : 'bg-surface-input text-text-muted cursor-not-allowed'
          }`}>
          {saving ? 'Saving...' : 'Save Commands'}
        </button>
        {success && <span className="text-[10px] text-state-healthy">Saved</span>}
        {error && <span className="text-[10px] text-state-error">{error}</span>}
      </div>
    </div>
  )
}

// ── UAB Panel ───────────────────────────────────────────────────────

function UABPanel() {
  const [status, setStatus] = useState<UABStatus | null>(null)
  const [apps, setApps] = useState<UABConnectedApp[]>([])
  const [error, setError] = useState<string | null>(null)
  const inFlightRef = useRef(false)

  const poll = useCallback(async () => {
    if (inFlightRef.current) return

    inFlightRef.current = true
    try {
      const [s, a] = await Promise.all([fetchUABStatus(), fetchUABApps()])
      setStatus(s); setApps(a.apps || []); setError(null)
    } catch {
      setError('Failed to check UAB status')
    } finally {
      inFlightRef.current = false
    }
  }, [])

  useEffect(() => { poll(); const t = setInterval(poll, 5000); return () => clearInterval(t) }, [poll])

  const reachable = status?.reachable ?? false

  return (
    <div className="mt-2 space-y-3">
      <div className="p-3 bg-surface-card rounded-lg border border-border-default">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">UAB Daemon</span>
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${reachable ? 'bg-state-healthy animate-pulse' : 'bg-state-error'}`} />
            <span className={`text-[10px] font-medium ${reachable ? 'text-state-healthy' : 'text-state-error'}`}>{reachable ? 'Running' : 'Offline'}</span>
          </div>
        </div>
        {reachable && status && (
          <div className="space-y-1.5 mb-3">
            <div className="flex items-center justify-between"><span className="text-[10px] text-text-muted">Version</span><span className="text-[10px] font-mono text-text-primary">v{status.version}</span></div>
            <div className="flex items-center justify-between"><span className="text-[10px] text-text-muted">Connected Apps</span><span className="text-[10px] font-mono text-text-primary">{status.connected_apps}</span></div>
            <div className="flex items-center justify-between"><span className="text-[10px] text-text-muted">Frameworks</span><span className="text-[10px] font-mono text-text-primary">{status.supported_frameworks.length > 0 ? status.supported_frameworks.join(', ') : 'none'}</span></div>
            {status.transport && (
              <div className="flex items-center justify-between"><span className="text-[10px] text-text-muted">Transport</span><span className="text-[10px] font-mono text-text-primary">{status.transport}</span></div>
            )}
            {!!status.standalone_features?.length && (
              <div className="flex items-center justify-between"><span className="text-[10px] text-text-muted">Bridge Features</span><span className="text-[10px] font-mono text-text-primary">{status.standalone_features.join(', ')}</span></div>
            )}
            {status.uptime_seconds > 0 && (
              <div className="flex items-center justify-between"><span className="text-[10px] text-text-muted">Uptime</span><span className="text-[10px] font-mono text-text-primary">{Math.floor(status.uptime_seconds / 60)}m {status.uptime_seconds % 60}s</span></div>
            )}
          </div>
        )}
        {!reachable && (
          <div className="space-y-2">
            <p className="text-[10px] text-text-muted leading-relaxed">The UAB daemon is not running. Install for auto-start:</p>
            <code className="block text-[10px] font-mono bg-surface-input rounded px-2 py-1.5 text-text-primary select-all">scripts\install-uab.bat</code>
            <p className="text-[10px] text-text-muted">Or start manually (foreground): <code className="font-mono text-text-primary">scripts\start-uab.bat</code></p>
          </div>
        )}
      </div>
      {reachable && apps.length > 0 && (
        <div className="p-3 bg-surface-card rounded-lg border border-border-default">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">Connected Apps</span>
            <span className="text-[10px] text-text-muted">{apps.length} app{apps.length !== 1 ? 's' : ''}</span>
          </div>
          <div className="space-y-1.5">
            {apps.map((app) => (
              <div key={app.pid} className="flex items-center justify-between py-1 px-2 bg-surface-input rounded">
                <div className="flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-state-healthy" />
                  <span className="text-[10px] font-medium text-text-primary">{app.name}</span>
                  <span className="text-[9px] font-mono text-text-muted">PID {app.pid}</span>
                  {!!app.windowTitle && <span className="text-[9px] text-text-muted truncate max-w-[12rem]">{app.windowTitle}</span>}
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent-primary/10 text-accent-primary font-mono">{app.framework}</span>
                  <span className="text-[9px] text-text-muted">{app.connectionMethod}</span>
                  {typeof app.elementCount === 'number' && app.elementCount > 0 && <span className="text-[9px] text-text-muted">{app.elementCount} el</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="p-2 bg-surface-input rounded border border-border-default">
        <p className="text-[9px] text-text-muted leading-relaxed">
          UAB is licensed under <span className="font-medium text-text-secondary">Business Source License 1.1 (BSL 1.1)</span> — free for personal use and the Lancelot ecosystem.
          Commercial agent runtimes and enterprise use require a separate license. See packages/uab/LICENSE.
        </p>
      </div>
      {error && <p className="text-[10px] text-state-error">{error}</p>}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════
// Main Kill Switches Page — 4-Layer Progressive Disclosure
// ══════════════════════════════════════════════════════════════════════

export function KillSwitches() {
  usePageTitle('Kill Switches')

  // Data sources
  const { data: sysData } = usePolling({ fetcher: fetchSystemStatus, interval: 10000 })
  const { data: flagsData, refetch: refetchFlags } = usePolling({ fetcher: fetchFlags, interval: 10000 })
  const { data: crusaderStatus } = usePolling<CrusaderStatusResponse>({ fetcher: fetchCrusaderStatus, interval: 5000 })

  // UI state
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [pendingToggle, setPendingToggle] = useState<string | null>(null)
  const [pendingDanger, setPendingDanger] = useState<{ name: string; message: string } | null>(null)
  const [restartBanner, setRestartBanner] = useState<string | null>(null)
  const [agentStartHint, setAgentStartHint] = useState<string | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)

  // Search state
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  // Accordion state (persisted in sessionStorage)
  const [accordionState, setAccordionState] = useState<Record<string, boolean>>(() => {
    try {
      const saved: Record<string, boolean> = {}
      for (const cat of CATEGORY_ORDER) {
        const v = sessionStorage.getItem(`ks_accordion_${cat}`)
        saved[cat] = v === 'true'
      }
      return saved
    } catch { return {} }
  })

  const crusaderActive = crusaderStatus?.crusader_mode ?? false
  const overriddenFlags = new Set(crusaderStatus?.overridden_flags ?? [])
  const flags = flagsData?.flags ?? {}

  // ── Derived data ────────────────────────────────────────────

  const allSwitches = useMemo(() => {
    return Object.entries(flags).filter(([, info]) => !info.hidden)
  }, [flags])

  const masterSwitches = useMemo(() => {
    return allSwitches.filter(([name]) => MASTER_SWITCH_NAMES.has(name))
  }, [allSwitches])

  const categorySwitches = useMemo(() => {
    // Exclude master switches from category accordions
    const nonMaster = allSwitches.filter(([name]) => !MASTER_SWITCH_NAMES.has(name))
    const grouped: Record<string, [string, FlagInfo][]> = {}
    for (const [name, info] of nonMaster) {
      const cat = info.category || 'Other'
      if (!grouped[cat]) grouped[cat] = []
      grouped[cat].push([name, info])
    }
    return grouped
  }, [allSwitches])

  // Show known categories in order, then any unknown categories alphabetically
  const knownCats = CATEGORY_ORDER.filter(c => categorySwitches[c]?.length)
  const unknownCats = Object.keys(categorySwitches).filter(c => !CATEGORY_ORDER.includes(c)).sort()
  const sortedCategories = [...knownCats, ...unknownCats]

  // Stats for status header
  const totalCount = allSwitches.length
  const activeCount = allSwitches.filter(([, info]) => info.enabled).length
  const depsUnmetCount = allSwitches.filter(([, info]) =>
    info.requires.some(dep => !flags[dep]?.enabled)
  ).length
  const criticalOff = allSwitches
    .filter(([name, info]) => !info.enabled && (MASTER_SWITCH_NAMES.has(name) || GOVERNANCE_FLAGS.has(name)))
    .map(([name]) => name)

  // ── Search + filter logic ───────────────────────────────────

  const isSearchActive = searchQuery.trim().length > 0 || statusFilter !== 'all'

  const matchesSearch = useCallback((name: string, info: FlagInfo, category: string): boolean => {
    const q = searchQuery.toLowerCase().trim()
    if (!q) return true
    // Special queries
    if (q === 'off') return !info.enabled
    if (q === 'deps') return info.requires.some(dep => !flags[dep]?.enabled)
    return (
      name.toLowerCase().includes(q) ||
      (info.description || '').toLowerCase().includes(q) ||
      category.toLowerCase().includes(q)
    )
  }, [searchQuery, flags])

  const matchesFilter = useCallback((info: FlagInfo): boolean => {
    if (statusFilter === 'active') return info.enabled
    if (statusFilter === 'inactive') return !info.enabled
    if (statusFilter === 'deps_unmet') return info.requires.some(dep => !flags[dep]?.enabled)
    return true
  }, [statusFilter, flags])

  // Filtered master switches
  const filteredMaster = useMemo(() => {
    if (!isSearchActive) return masterSwitches
    return masterSwitches.filter(([name, info]) => matchesSearch(name, info, 'Master') && matchesFilter(info))
  }, [isSearchActive, masterSwitches, matchesSearch, matchesFilter])

  // Filtered category switches
  const filteredCategories = useMemo(() => {
    if (!isSearchActive) return categorySwitches
    const result: Record<string, [string, FlagInfo][]> = {}
    for (const [cat, switches] of Object.entries(categorySwitches)) {
      const filtered = switches.filter(([name, info]) => matchesSearch(name, info, cat) && matchesFilter(info))
      if (filtered.length > 0) result[cat] = filtered
    }
    return result
  }, [isSearchActive, categorySwitches, matchesSearch, matchesFilter])

  const filteredSortedCategories = isSearchActive
    ? CATEGORY_ORDER.filter(c => filteredCategories[c]?.length)
    : sortedCategories

  const noResults = isSearchActive &&
    filteredMaster.length === 0 &&
    filteredSortedCategories.length === 0

  // ── Handlers ────────────────────────────────────────────────

  const toggleExpand = (name: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const toggleAccordion = (cat: string) => {
    setAccordionState(prev => {
      const next = { ...prev, [cat]: !prev[cat] }
      try { sessionStorage.setItem(`ks_accordion_${cat}`, String(next[cat])) } catch { /* */ }
      return next
    })
  }

  const handleToggle = (e: React.MouseEvent, name: string, info: FlagInfo) => {
    e.stopPropagation()
    if (!info.enabled && info.confirm_enable) {
      setPendingDanger({ name, message: info.confirm_enable })
      return
    }
    if (info.restart_required) {
      setPendingToggle(name)
      return
    }
    doToggle(name)
  }

  const doToggle = async (name: string) => {
    setToggling(name)
    setPendingToggle(null)
    try {
      const res = await toggleFlag(name)
      if (res.restart_required) setRestartBanner(res.message)
      if (res.agent_reachable === false && res.agent_start_hint) {
        setAgentStartHint(res.agent_start_hint)
      } else {
        setAgentStartHint(null)
      }
      refetchFlags()
    } catch { /* */ }
    finally { setToggling(null) }
  }

  const clearSearch = useCallback(() => {
    setSearchQuery('')
    setStatusFilter('all')
  }, [])

  // ── Render ──────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-4">
      <h2 className="text-xl font-semibold text-text-primary">Kill Switches</h2>

      {/* Crusader Mode Banner */}
      {crusaderActive && (
        <div className="p-3 bg-accent-secondary/10 border border-accent-secondary/30 rounded-lg">
          <div className="flex items-center gap-2 mb-1">
            <span className="w-2 h-2 rounded-full bg-accent-secondary animate-pulse" />
            <span className="text-sm font-semibold text-accent-secondary">Crusader Mode Active</span>
          </div>
          <p className="text-xs text-text-secondary">
            {overriddenFlags.size} flag{overriddenFlags.size !== 1 ? 's' : ''} overridden. Locked until deactivated.
          </p>
          {overriddenFlags.size > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {Array.from(overriddenFlags).map(f => (
                <span key={f} className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-accent-secondary/15 text-accent-secondary">
                  {f.replace('FEATURE_', '')}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Restart / Agent Banners */}
      {restartBanner && (
        <div className="p-3 bg-state-degraded/10 border border-state-degraded/30 rounded-lg flex items-center justify-between">
          <span className="text-sm text-state-degraded">{restartBanner}</span>
          <button onClick={() => setRestartBanner(null)} className="text-xs text-text-muted hover:text-text-primary ml-4">Dismiss</button>
        </div>
      )}
      {agentStartHint && (
        <div className="p-3 bg-state-degraded/10 border border-state-degraded/30 rounded-lg">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-state-degraded animate-pulse" />
              <span className="text-sm font-semibold text-state-degraded">Host Agent Not Running</span>
            </div>
            <button onClick={() => setAgentStartHint(null)} className="text-xs text-text-muted hover:text-text-primary">Dismiss</button>
          </div>
          <p className="text-xs text-text-secondary mb-2">Host Bridge is enabled but the agent process is not running.</p>
          <code className="block text-[10px] font-mono bg-surface-input rounded px-2 py-1.5 text-text-primary select-all">host_agent\start_agent.bat</code>
        </div>
      )}

      {/* ═══ Layer 1: Status Header ═══ */}
      <StatusHeader
        totalCount={totalCount}
        activeCount={activeCount}
        depsUnmetCount={depsUnmetCount}
        criticalOff={criticalOff}
      />

      {/* ═══ Layer 2: Search ═══ */}
      <SearchBar
        value={searchQuery}
        onChange={setSearchQuery}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        onClear={clearSearch}
      />

      {/* ═══ Layer 3: Master Switches ═══ */}
      {filteredMaster.length > 0 && (
        <div className="border-l-4 border-state-degraded/60 rounded-lg bg-surface-card p-4 space-y-2">
          <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">Master Switches</h3>
          {filteredMaster.map(([name, info]) => (
            <MasterSwitchRow
              key={name}
              name={name}
              info={info}
              flags={flags}
              isCrusaderOverride={overriddenFlags.has(name)}
              isToggling={toggling === name}
              onToggle={handleToggle}
            />
          ))}
        </div>
      )}

      {/* ═══ Layer 4: Category Accordions ═══ */}
      {filteredSortedCategories.map(category => (
        <CategoryAccordion
          key={category}
          category={category}
          switches={filteredCategories[category] ?? categorySwitches[category] ?? []}
          flags={flags}
          isOpen={accordionState[category] ?? false}
          onToggleOpen={() => toggleAccordion(category)}
          isSearchActive={isSearchActive}
          expanded={expanded}
          onToggleExpand={toggleExpand}
          overriddenFlags={overriddenFlags}
          toggling={toggling}
          onToggle={handleToggle}
        />
      ))}

      {/* No results */}
      {noResults && (
        <div className="bg-surface-card border border-border-default rounded-lg p-8 text-center">
          <p className="text-sm text-text-muted">No switches match "{searchQuery}"</p>
          <button onClick={clearSearch} className="mt-2 text-xs text-accent-primary hover:underline">Clear search</button>
        </div>
      )}

      {/* System State (collapsed into accordion) */}
      {!isSearchActive && sysData && (
        <details className="bg-surface-card border border-border-default rounded-lg overflow-hidden">
          <summary className="p-3 cursor-pointer text-sm font-medium text-text-muted uppercase tracking-wider hover:text-text-secondary transition-colors">
            System State
          </summary>
          <div className="p-3 border-t border-border-default space-y-2">
            <div className="flex items-center justify-between p-2 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">Onboarding</span>
              <span className={`text-xs font-medium ${sysData.onboarding?.is_ready ? 'text-state-healthy' : 'text-state-degraded'}`}>
                {sysData.onboarding?.state ?? 'Unknown'}
              </span>
            </div>
            <div className="flex items-center justify-between p-2 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">System Ready</span>
              <span className={`text-xs font-medium ${sysData.onboarding?.is_ready ? 'text-state-healthy' : 'text-text-muted'}`}>
                {sysData.onboarding?.is_ready ? 'Ready' : 'Not Ready'}
              </span>
            </div>
            <div className="flex items-center justify-between p-2 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">Cooldown</span>
              <span className={`text-xs font-medium ${sysData.cooldown?.active ? 'text-state-degraded' : 'text-state-healthy'}`}>
                {sysData.cooldown?.active ? `Active (${Math.round(sysData.cooldown.remaining_seconds)}s)` : 'Inactive'}
              </span>
            </div>
          </div>
        </details>
      )}

      {/* Confirm Dialogs */}
      <ConfirmDialog
        open={pendingToggle !== null}
        title="Toggle Startup Flag"
        description={`${pendingToggle ?? ''} controls a startup subsystem. Toggling it will change the in-memory flag immediately, but a container restart is required for the subsystem to fully initialize or shut down. Continue?`}
        variant="destructive"
        confirmLabel="Toggle"
        onConfirm={() => pendingToggle && doToggle(pendingToggle)}
        onCancel={() => setPendingToggle(null)}
      />

      <ConfirmDialog
        open={pendingDanger !== null}
        title="Accept Risk"
        description={pendingDanger?.message ?? ''}
        variant="destructive"
        confirmLabel="I Accept the Risk"
        onConfirm={() => { if (pendingDanger) { doToggle(pendingDanger.name); setPendingDanger(null) } }}
        onCancel={() => setPendingDanger(null)}
      />
    </div>
  )
}
