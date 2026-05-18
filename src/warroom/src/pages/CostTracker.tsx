import { useState, useEffect, useCallback } from 'react'
import type { ReactNode } from 'react'
import {
  Activity,
  CheckCircle2,
  Clock3,
  KeyRound,
  RefreshCw,
  RotateCcw,
  ServerCog,
  ShieldCheck,
  WalletCards,
  Zap,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { usePolling, usePageTitle } from '@/hooks'
import {
  fetchUsageSummary, fetchUsageLanes, fetchUsageModels, fetchUsageMonthly,
  fetchProviderStack, refreshModelDiscovery,
  fetchAvailableProviders, switchProvider, overrideLane, resetLanes,
  fetchProviderKeys, rotateProviderKey,
  fetchLocalOpenAIConfig, saveLocalOpenAIConfig,
  initiateOAuth, fetchOAuthStatus, revokeOAuth,
  initiateCodexOAuth, fetchCodexOAuthStatus, revokeCodexOAuth,
} from '@/api'
import type { DiscoveredModel, AvailableProvider, ProviderKeyInfo, OAuthStatusResponse, LocalOpenAIConfig } from '@/api'
import { MetricCard } from '@/components'
import { formatTimeOnly } from '@/utils/dateFormat'
import { getErrorMessage } from '@/utils/errors'

/** Format context window size for display */
function formatCtx(tokens: number): string {
  if (!tokens) return '--'
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`
  return String(tokens)
}

/** Lane display order and labels */
const LANE_ORDER = ['fast', 'deep', 'cache'] as const
const LANE_LABELS: Record<string, string> = {
  fast: 'Fast',
  deep: 'Deep',
  cache: 'Cache',
  flagship_fast: 'Flagship Fast',
  flagship_deep: 'Flagship Deep',
  local_redaction: 'Local Redaction',
  local_utility: 'Local Utility',
  local_agentic: 'Local Agentic',
  unclassified: 'Unclassified',
  legacy_unclassified: 'Legacy Unclassified',
}

const PRIMARY_USAGE_LANE_ALIASES: Record<string, string[]> = {
  fast: ['fast', 'flagship_fast'],
  deep: ['deep', 'flagship_deep'],
  cache: ['cache'],
}

const PRIMARY_USAGE_LANE_KEYS = new Set(
  Object.values(PRIMARY_USAGE_LANE_ALIASES).flat()
)

type UsageMetrics = {
  calls: number
  tokens: number
  cost: number
}

function laneSourceLabel(source?: string): string {
  if (source === 'override') return 'Pinned'
  if (source === 'fallback') return 'Default'
  return 'Auto'
}

function connectionLabel(status?: string): string {
  if (status === 'connected') return 'Connected'
  if (status === 'auth_error') return 'Invalid API Key'
  if (status === 'no_key') return 'No API Key'
  return 'Unavailable'
}

function connectionTone(status?: string): string {
  if (status === 'connected') return 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy'
  if (status === 'auth_error' || status === 'no_key') return 'border-state-error/30 bg-state-error/10 text-state-error'
  return 'border-border-default bg-surface-card-elevated text-text-muted'
}

function laneDisplayName(lane: string): string {
  return LANE_LABELS[lane] || lane.replace(/_/g, ' ')
}

function readNumeric(data: Record<string, unknown>, fields: string[]): number {
  for (const field of fields) {
    const value = data[field]
    if (value != null && value !== '') return Number(value)
  }
  return 0
}

function readUsageRow(data: Record<string, unknown> | undefined): UsageMetrics {
  const d = data ?? {}
  return {
    calls: readNumeric(d, ['calls', 'requests']),
    tokens: readNumeric(d, ['tokens', 'tokens_est', 'total_tokens_est', 'total_tokens']),
    cost: readNumeric(d, ['estimated_cost', 'cost', 'total_cost_est', 'total_cost']),
  }
}

function sumUsageRows(rows: Array<Record<string, unknown> | undefined>): UsageMetrics {
  return rows.reduce<UsageMetrics>(
    (total, row) => {
      const current = readUsageRow(row)
      return {
        calls: total.calls + current.calls,
        tokens: total.tokens + current.tokens,
        cost: total.cost + current.cost,
      }
    },
    { calls: 0, tokens: 0, cost: 0 }
  )
}

function SectionTitle({
  icon: Icon,
  label,
  action,
}: {
  icon: LucideIcon
  label: string
  action?: ReactNode
}) {
  return (
    <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <h3 className="inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
        <Icon className="h-4 w-4 text-accent-primary" aria-hidden="true" />
        {label}
      </h3>
      {action}
    </div>
  )
}

function StatusBadge({
  children,
  tone = 'muted',
}: {
  children: ReactNode
  tone?: 'healthy' | 'error' | 'accent' | 'warning' | 'muted'
}) {
  const tones = {
    healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
    error: 'border-state-error/30 bg-state-error/10 text-state-error',
    accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
    warning: 'border-state-degraded/30 bg-state-degraded/10 text-state-degraded',
    muted: 'border-border-default bg-surface-input text-text-muted',
  }
  return (
    <span className={`inline-flex items-center rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider ${tones[tone]}`}>
      {children}
    </span>
  )
}

export function CostTracker() {
  usePageTitle('Cost Tracker')
  const { data: summary } = usePolling({ fetcher: fetchUsageSummary, interval: 15000 })
  const { data: lanes } = usePolling({ fetcher: fetchUsageLanes, interval: 30000 })
  const { data: models } = usePolling({ fetcher: fetchUsageModels, interval: 30000 })
  const { data: monthly } = usePolling({ fetcher: () => fetchUsageMonthly(), interval: 60000 })
  const { data: stack, refetch: refetchStack } = usePolling({ fetcher: fetchProviderStack, interval: 60000 })

  const [providers, setProviders] = useState<AvailableProvider[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [switching, setSwitching] = useState(false)
  const [laneLoading, setLaneLoading] = useState<string | null>(null)
  const [statusMsg, setStatusMsg] = useState<string | null>(null)

  // Key management state
  const [providerKeys, setProviderKeys] = useState<ProviderKeyInfo[]>([])
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [newKeyValue, setNewKeyValue] = useState('')
  const [keyLoading, setKeyLoading] = useState(false)
  const [keyError, setKeyError] = useState<string | null>(null)
  const [localConfig, setLocalConfig] = useState<LocalOpenAIConfig | null>(null)
  const [localForm, setLocalForm] = useState({
    base_url: '',
    api_key: '',
    fast_model: 'local-fast',
    deep_model: 'local-deep',
    cache_model: 'local-cache',
    context_window: 32768,
    supports_tools: true,
  })
  const [localSaving, setLocalSaving] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  // OAuth state (Anthropic)
  const [oauthStatus, setOauthStatus] = useState<OAuthStatusResponse | null>(null)
  const [oauthLoading, setOauthLoading] = useState(false)

  // OpenAI Codex OAuth state
  const [codexOauthStatus, setCodexOauthStatus] = useState<OAuthStatusResponse | null>(null)
  const [codexOauthLoading, setCodexOauthLoading] = useState(false)

  const showStatus = useCallback((msg: string) => {
    setStatusMsg(msg)
    setTimeout(() => setStatusMsg(null), 4000)
  }, [])

  const loadProviders = useCallback(async (failureMessage = 'Failed to load available providers') => {
    try {
      const res = await fetchAvailableProviders()
      setProviders(res.providers ?? [])
    } catch (error) {
      showStatus(getErrorMessage(error, failureMessage))
    }
  }, [showStatus])

  const loadProviderKeys = useCallback(async (failureMessage = 'Failed to load provider keys') => {
    try {
      const res = await fetchProviderKeys()
      setProviderKeys(res.keys ?? [])
    } catch (error) {
      showStatus(getErrorMessage(error, failureMessage))
    }
  }, [showStatus])

  const loadLocalConfig = useCallback(async () => {
    try {
      const config = await fetchLocalOpenAIConfig()
      setLocalConfig(config)
      setLocalForm({
        base_url: config.base_url || '',
        api_key: '',
        fast_model: config.fast_model || 'local-fast',
        deep_model: config.deep_model || 'local-deep',
        cache_model: config.cache_model || 'local-cache',
        context_window: config.context_window || 32768,
        supports_tools: config.supports_tools,
      })
    } catch (error) {
      showStatus(getErrorMessage(error, 'Failed to load local model provider config'))
    }
  }, [showStatus])

  // Fetch available providers on mount
  useEffect(() => {
    void loadProviders()
    void loadProviderKeys()
    void loadLocalConfig()
    // Fetch OAuth status
    fetchOAuthStatus()
      .then(res => setOauthStatus(res))
      .catch((error) => showStatus(getErrorMessage(error, 'Failed to load Anthropic OAuth status')))
    // Fetch Codex OAuth status
    fetchCodexOAuthStatus()
      .then(res => setCodexOauthStatus(res))
      .catch((error) => showStatus(getErrorMessage(error, 'Failed to load Codex OAuth status')))
  }, [loadLocalConfig, loadProviderKeys, loadProviders, showStatus])

  // Re-fetch providers after stack changes (to update active indicator)
  const refreshProviders = useCallback(() => {
    return loadProviders('Failed to refresh available providers')
  }, [loadProviders])

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await refreshModelDiscovery()
      await refetchStack()
      await refreshProviders()
    } catch (error) {
      showStatus(getErrorMessage(error, 'Model discovery refresh failed'))
    } finally {
      setRefreshing(false)
    }
  }

  const handleSwitchProvider = async (providerName: string) => {
    setSwitching(true)
    setStatusMsg(null)
    try {
      const res = await switchProvider(providerName)
      if (res.status === 'ok') {
        showStatus(res.message || `Switched to ${providerName}`)
        await refetchStack()
        await refreshProviders()
      } else {
        showStatus(res.message || 'Switch failed')
      }
    } catch (error) {
      showStatus(getErrorMessage(error, 'Provider switch failed'))
    } finally {
      setSwitching(false)
    }
  }

  const handleLaneOverride = async (lane: string, modelId: string) => {
    setLaneLoading(lane)
    setStatusMsg(null)
    try {
      const res = await overrideLane(lane, modelId)
      if (res.status === 'ok') {
        showStatus(res.message || `Lane ${lane} updated`)
        await refetchStack()
      } else {
        showStatus(res.message || 'Override failed')
      }
    } catch {
      showStatus('Lane override failed')
    } finally {
      setLaneLoading(null)
    }
  }

  const handleResetLanes = async () => {
    setStatusMsg(null)
    try {
      const res = await resetLanes()
      if (res.status === 'ok') {
        showStatus(res.message || 'Lanes reset to auto')
        await refetchStack()
      } else {
        showStatus(res.message || 'Reset failed')
      }
    } catch {
      showStatus('Lane reset failed')
    }
  }

  const handleRotateKey = async (provider: string) => {
    if (!newKeyValue.trim()) return
    setKeyLoading(true)
    setKeyError(null)
    try {
      const res = await rotateProviderKey(provider, newKeyValue.trim())
      if (res.status === 'ok') {
        showStatus(res.message || `Key rotated for ${provider}`)
        setEditingKey(null)
        setNewKeyValue('')
        // Refresh key list, providers, and stack status
        await loadProviderKeys('Failed to refresh provider keys')
        await refreshProviders()
        await refetchStack()
      } else {
        setKeyError(res.message || 'Rotation failed')
      }
    } catch (error) {
      setKeyError(getErrorMessage(error, 'Key rotation failed — check the key and try again'))
    } finally {
      setKeyLoading(false)
    }
  }

  const handleLocalConfigSave = async () => {
    setLocalSaving(true)
    setLocalError(null)
    try {
      const res = await saveLocalOpenAIConfig({
        base_url: localForm.base_url.trim(),
        api_key: localForm.api_key.trim() || undefined,
        fast_model: localForm.fast_model.trim(),
        deep_model: localForm.deep_model.trim(),
        cache_model: localForm.cache_model.trim() || localForm.fast_model.trim(),
        context_window: Number(localForm.context_window) || 32768,
        supports_tools: localForm.supports_tools,
      })
      if (res.status === 'ok') {
        setLocalConfig(res.config)
        setLocalForm(prev => ({ ...prev, api_key: '' }))
        showStatus(res.message || 'Local provider config saved')
        await loadProviderKeys('Failed to refresh provider keys')
        await refreshProviders()
        await refetchStack()
      } else {
        setLocalError(res.message || 'Local provider config save failed')
      }
    } catch (error) {
      setLocalError(getErrorMessage(error, 'Local provider config save failed'))
    } finally {
      setLocalSaving(false)
    }
  }

  // OAuth handlers
  const handleOAuthSetup = async () => {
    setOauthLoading(true)
    try {
      const res = await initiateOAuth()
      if (res.status === 'ok' && res.auth_url) {
        window.open(res.auth_url, '_blank', 'noopener,noreferrer')
        showStatus('Opened Anthropic authorization in new tab…')
        // Poll for completion
        const pollId = setInterval(async () => {
          try {
            const status = await fetchOAuthStatus()
            setOauthStatus(status)
            if (status.configured) {
              clearInterval(pollId)
              showStatus('OAuth authorized successfully!')
              await loadProviderKeys('Failed to refresh provider keys')
              await refreshProviders()
              await refetchStack()
            }
          } catch (error) {
            clearInterval(pollId)
            showStatus(getErrorMessage(error, 'Failed to refresh Anthropic OAuth status'))
          }
        }, 3000)
        // Stop polling after 5 minutes
        setTimeout(() => clearInterval(pollId), 300000)
      } else {
        showStatus(res.message || 'Failed to initiate OAuth')
      }
    } catch {
      showStatus('OAuth initiation failed')
    } finally {
      setOauthLoading(false)
    }
  }

  const handleOAuthRevoke = async () => {
    try {
      await revokeOAuth()
      setOauthStatus({ configured: false, status: 'not_configured' })
      showStatus('OAuth tokens revoked')
      await loadProviderKeys('Failed to refresh provider keys')
    } catch (error) {
      showStatus(getErrorMessage(error, 'Failed to revoke OAuth'))
    }
  }

  // OpenAI Codex OAuth handlers
  const handleCodexOAuthSetup = async () => {
    setCodexOauthLoading(true)
    try {
      const res = await initiateCodexOAuth()
      if (res.status === 'ok' && res.auth_url) {
        window.open(res.auth_url, '_blank', 'noopener,noreferrer')
        showStatus('Opened ChatGPT authorization in new tab…')
        // Poll for completion
        const pollId = setInterval(async () => {
          try {
            const status = await fetchCodexOAuthStatus()
            setCodexOauthStatus(status)
            if (status.configured) {
              clearInterval(pollId)
              showStatus('Codex OAuth authorized successfully!')
              await loadProviderKeys('Failed to refresh provider keys')
              await refreshProviders()
              await refetchStack()
            }
          } catch (error) {
            clearInterval(pollId)
            showStatus(getErrorMessage(error, 'Failed to refresh Codex OAuth status'))
          }
        }, 3000)
        setTimeout(() => clearInterval(pollId), 300000)
      } else if (res.status === 'ok') {
        showStatus(res.message || 'Codex auth is already available')
        const status = await fetchCodexOAuthStatus()
        setCodexOauthStatus(status)
        await loadProviderKeys('Failed to refresh provider keys')
        await refreshProviders()
        await refetchStack()
      } else {
        showStatus(res.message || 'Failed to initiate Codex OAuth')
      }
    } catch {
      showStatus('Codex OAuth initiation failed')
    } finally {
      setCodexOauthLoading(false)
    }
  }

  const handleCodexOAuthRevoke = async () => {
    try {
      await revokeCodexOAuth()
      setCodexOauthStatus({ configured: false, status: 'not_configured' })
      showStatus('Codex OAuth tokens revoked')
      await loadProviderKeys('Failed to refresh provider keys')
    } catch (error) {
      showStatus(getErrorMessage(error, 'Failed to revoke Codex OAuth'))
    }
  }

  const usage = summary?.usage ?? {} as Record<string, unknown>
  const laneData = (lanes?.lanes ?? {}) as Record<string, Record<string, unknown>>
  const modelData = models?.models ?? {}
  const monthlyData = monthly?.monthly ?? {} as Record<string, unknown>

  // Backend field names: total_requests, total_tokens_est, total_cost_est, avg_elapsed_ms
  const totalRequests = (usage as Record<string, unknown>).total_requests
  const totalTokens = (usage as Record<string, unknown>).total_tokens_est
  const estCost = (usage as Record<string, unknown>).total_cost_est
  const avgLatency = (usage as Record<string, unknown>).avg_elapsed_ms

  // Monthly data fields
  const md = monthlyData as Record<string, unknown>
  const byModel = (md.by_model ?? {}) as Record<string, Record<string, unknown>>
  const monthlyByLane = (md.by_lane ?? {}) as Record<string, Record<string, unknown>>
  const byDay = (md.by_day ?? {}) as Record<string, Record<string, unknown>>

  // Use monthly by_model / summary by_model as fallback for per-model breakdown
  const summaryByModel = (usage as Record<string, unknown>).by_model as Record<string, Record<string, unknown>> | undefined
  const effectiveModelData = Object.keys(modelData).length > 0
    ? modelData
    : (Object.keys(byModel).length > 0 ? byModel : (summaryByModel ?? {}))

  // Use summary by_lane as fallback for per-lane breakdown
  const summaryByLane = (usage as Record<string, unknown>).by_lane as Record<string, Record<string, unknown>> | undefined
  const liveLaneData = Object.keys(laneData).length > 0 ? laneData : (summaryByLane ?? {})
  const usingMonthlyLaneFallback = Object.keys(liveLaneData).length === 0 && Object.keys(monthlyByLane).length > 0
  const effectiveLaneData: Record<string, Record<string, unknown>> = usingMonthlyLaneFallback ? monthlyByLane : liveLaneData

  // Model stack data
  const stackLanes = stack?.lanes ?? {}
  const discoveredModels = stack?.discovered_models ?? []

  // Format last refresh time
  const lastRefresh = stack?.last_refresh
    ? formatTimeOnly(stack.last_refresh)
    : null
  const configuredKeyCount = providerKeys.filter(k => k.has_key || k.oauth_configured).length
  const activeProviderName = stack?.provider_display_name || stack?.provider || 'No provider'
  const activeLaneCount = Object.keys(stackLanes).length
  const discoveredCount = discoveredModels.length
  const primaryLaneRows = LANE_ORDER.map(lane => {
    const aliases = PRIMARY_USAGE_LANE_ALIASES[lane] ?? [lane]
    return {
      lane,
      label: LANE_LABELS[lane],
      model: stackLanes[lane]?.display_name || stackLanes[lane]?.model || '',
      usage: sumUsageRows(aliases.map(alias => effectiveLaneData[alias])),
    }
  })
  const nonPrimaryLaneEntries = Object.entries(effectiveLaneData).filter(
    ([lane]) => !PRIMARY_USAGE_LANE_KEYS.has(lane)
  )
  const hasAnyLaneUsage = primaryLaneRows.some(row => row.usage.calls > 0 || row.usage.tokens > 0 || row.usage.cost > 0)
    || nonPrimaryLaneEntries.length > 0

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-accent-primary">
              <WalletCards className="h-4 w-4" aria-hidden="true" />
              Spend Control
            </div>
            <h1 className="mt-2 text-2xl font-semibold text-text-primary">Cost Tracker</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-text-secondary">
              Monitor model spend, keep provider access healthy, and pin lane models when the automatic stack needs operator direction.
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-3 xl:min-w-[34rem]">
            <div className="rounded border border-border-default bg-surface-card-elevated p-3">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Active Provider</div>
              <div className="mt-1 truncate text-sm font-semibold text-text-primary" title={activeProviderName}>
                {activeProviderName}
              </div>
            </div>
            <div className={`rounded border p-3 ${connectionTone(stack?.status)}`}>
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Provider State</div>
              <div className="mt-1 text-sm font-semibold">{connectionLabel(stack?.status)}</div>
            </div>
            <div className="rounded border border-border-default bg-surface-card-elevated p-3">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Keys / OAuth</div>
              <div className="mt-1 text-sm font-semibold text-text-primary">{configuredKeyCount} configured</div>
            </div>
          </div>
        </div>
      </section>

      {/* Status message toast */}
      {statusMsg && (
        <div className="rounded border border-accent-primary/30 bg-accent-primary/15 px-4 py-2 text-sm text-accent-primary">
          {statusMsg}
        </div>
      )}

      {/* Summary Metrics */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Total Calls" value={totalRequests != null ? String(totalRequests) : '--'} />
        <MetricCard label="Total Tokens" value={totalTokens ? Number(totalTokens).toLocaleString() : '--'} />
        <MetricCard label="Est. Cost" value={estCost ? `$${Number(estCost).toFixed(4)}` : '--'} />
        <MetricCard label="Avg Latency" value={avgLatency ? `${Math.round(Number(avgLatency))}ms` : '--'} />
      </div>

      {/* ======= Model Stack Section ======= */}
      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <SectionTitle
          icon={ServerCog}
          label="Model Stack"
          action={
            <div className="flex flex-wrap items-center gap-2">
              {lastRefresh && (
                <span className="inline-flex items-center gap-1.5 text-xs text-text-muted">
                  <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
                  {lastRefresh}
                </span>
              )}
              <button
                onClick={handleResetLanes}
                className="inline-flex items-center gap-2 rounded border border-border-default px-3 py-1.5 text-xs text-text-secondary transition-colors hover:bg-surface-card-elevated hover:text-text-primary"
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                Auto
              </button>
              <button
                onClick={handleRefresh}
                disabled={refreshing}
                className="inline-flex items-center gap-2 rounded border border-border-default px-3 py-1.5 text-xs text-text-secondary transition-colors hover:bg-surface-card-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
                {refreshing ? 'Refreshing' : 'Refresh'}
              </button>
            </div>
          }
        />
        <div className="mb-4 grid gap-3 lg:grid-cols-[minmax(0,1.4fr)_minmax(15rem,0.8fr)_minmax(15rem,0.8fr)]">
          <div className="rounded border border-border-default bg-surface-card-elevated p-3">
            <div className="mb-2 text-[10px] uppercase tracking-wider text-text-muted">Provider</div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <select
                value={stack?.provider || ''}
                onChange={(e) => handleSwitchProvider(e.target.value)}
                disabled={switching || providers.length === 0}
                className="min-h-9 flex-1 rounded border border-border-default bg-surface-input px-3 py-2 text-sm font-medium text-text-primary focus:outline-none focus:ring-1 focus:ring-accent-primary disabled:cursor-not-allowed disabled:opacity-50 [&>option]:bg-surface-input [&>option]:text-text-primary"
              >
                {providers.length === 0 && stack?.provider && (
                  <option value={stack.provider}>
                    {stack.provider_display_name || stack.provider}
                  </option>
                )}
                {providers.map(p => (
                  <option key={p.name} value={p.name} disabled={!p.has_key}>
                    {p.display_name}{!p.has_key ? ' (no key)' : ''}
                  </option>
                ))}
              </select>
              <span className={`inline-flex min-h-9 items-center justify-center rounded border px-3 text-xs font-medium ${connectionTone(stack?.status)}`}>
                {switching ? 'Switching' : connectionLabel(stack?.status)}
              </span>
            </div>
          </div>
          <div className="rounded border border-border-default bg-surface-card-elevated p-3">
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Lanes</div>
            <div className="mt-1 text-xl font-semibold text-text-primary">{activeLaneCount}</div>
            <div className="mt-1 text-xs text-text-muted">Fast, deep, and cache assignments.</div>
          </div>
          <div className="rounded border border-border-default bg-surface-card-elevated p-3">
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Models</div>
            <div className="mt-1 text-xl font-semibold text-text-primary">{discoveredCount}</div>
            <div className="mt-1 text-xs text-text-muted">Discovered for this provider.</div>
          </div>
        </div>

        {/* Lane assignments table with model selectors */}
        {Object.keys(stackLanes).length > 0 && (
          <div className="overflow-x-auto mb-4">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="text-text-muted text-left">
                  <th className="py-1 pr-4">Lane</th>
                  <th className="py-1 pr-4">Model</th>
                  <th className="py-1 pr-4">Mode</th>
                  <th className="py-1 pr-4 text-right">Context</th>
                  <th className="py-1 pr-4 text-right">$/1K out</th>
                  <th className="py-1 text-center">Tools</th>
                </tr>
              </thead>
              <tbody className="text-text-primary">
                {LANE_ORDER.filter(lane => stackLanes[lane]).map(lane => {
                  const l = stackLanes[lane]!
                  // Filter models for lane: fast/deep need tool support, cache doesn't
                  const requiresTools = lane === 'fast' || lane === 'deep'
                  const eligibleModels = discoveredModels.filter(
                    m => !requiresTools || m.supports_tools
                  )
                  const isLoading = laneLoading === lane
                  return (
                    <tr key={lane} className="border-t border-border-default/50">
                      <td className="py-2 pr-4 font-medium">{LANE_LABELS[lane] || lane}</td>
                      <td className="py-2 pr-4">
                        {eligibleModels.length > 0 ? (
                          <select
                            value={l.model}
                            onChange={(e) => handleLaneOverride(lane, e.target.value)}
                            disabled={isLoading}
                            className="bg-surface-input border border-border-default/50 rounded
                                       px-2 py-0.5 text-xs text-text-primary w-full max-w-[280px]
                                       focus:outline-none focus:ring-1 focus:ring-accent-primary
                                       disabled:opacity-50
                                       [&>option]:bg-surface-input [&>option]:text-text-primary"
                          >
                            {/* Always include the current model even if not in discovered list */}
                            {!eligibleModels.find(m => m.id === l.model) && (
                              <option value={l.model}>{l.display_name || l.model}</option>
                            )}
                            {eligibleModels.map(m => (
                              <option key={m.id} value={m.id}>
                                {m.display_name || m.id}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span>{l.display_name || l.model}</span>
                        )}
                        {isLoading && <span className="ml-2 text-text-muted">...</span>}
                      </td>
                      <td className="py-2 pr-4">
                        <span className={`inline-flex rounded border px-1.5 py-0.5 text-[10px] ${
                          l.source === 'override'
                            ? 'border-accent-primary/40 text-accent-primary'
                            : 'border-border-default text-text-muted'
                        }`}>
                          {laneSourceLabel(l.source)}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-right">{formatCtx(l.context_window)}</td>
                      <td className="py-2 pr-4 text-right">
                        {l.cost_output_per_1k ? `$${l.cost_output_per_1k.toFixed(4)}` : '--'}
                      </td>
                      <td className="py-2 text-center">
                        {l.supports_tools
                          ? <span className="inline-flex items-center justify-center gap-1 text-state-healthy"><CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" /> Yes</span>
                          : <span className="text-text-muted">No</span>
                        }
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Discovered models (collapsible) */}
        {discoveredModels.length > 0 && (
          <DiscoveredModelsList models={discoveredModels} />
        )}
      </section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(20rem,0.75fr)]">
      {/* ======= Provider Keys Section ======= */}
      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <SectionTitle icon={KeyRound} label="Provider API Keys" />
        <p className="text-xs text-text-muted mb-3">
          Rotate or add API keys for each provider. Keys are validated before saving and persisted to .env.
        </p>
        <div className="space-y-2">
          {providerKeys.length === 0 && (
            <p className="text-sm text-text-muted py-4 text-center">Loading provider keys...</p>
          )}
          {providerKeys.filter(k => !k.oauth_only && k.provider !== 'local-openai').map(k => (
            <div key={k.provider} className="flex flex-col gap-3 rounded border border-border-default bg-surface-card-elevated p-3 lg:flex-row lg:items-center">
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-text-primary">{k.display_name}</span>
                  {k.active && (
                    <StatusBadge tone="accent">Active</StatusBadge>
                  )}
                  {k.has_key ? (
                    <StatusBadge tone="healthy">Configured</StatusBadge>
                  ) : (
                    <StatusBadge tone="error">Not Set</StatusBadge>
                  )}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="text-xs text-text-muted font-mono">{k.env_var}</span>
                  {k.key_preview && (
                    <span className="text-xs text-text-muted font-mono">{k.key_preview}</span>
                  )}
                </div>
              </div>
              {editingKey === k.provider ? (
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <input
                    type="password"
                    value={newKeyValue}
                    onChange={e => { setNewKeyValue(e.target.value); setKeyError(null) }}
                    placeholder="Paste new API key..."
                    className="w-full rounded border border-border-default bg-surface-base px-3 py-1.5 text-xs font-mono text-text-primary sm:w-64
                               focus:outline-none focus:ring-1 focus:ring-accent-primary
                               placeholder:text-text-muted"
                    autoFocus
                    onKeyDown={e => e.key === 'Escape' && (setEditingKey(null), setNewKeyValue(''), setKeyError(null))}
                  />
                  <button
                    onClick={() => handleRotateKey(k.provider)}
                    disabled={keyLoading || !newKeyValue.trim()}
                    className="px-3 py-1.5 text-xs bg-accent-primary/15 text-accent-primary rounded
                               hover:bg-accent-primary/25 transition-colors
                               disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                  >
                    {keyLoading ? 'Validating...' : 'Save'}
                  </button>
                  <button
                    onClick={() => { setEditingKey(null); setNewKeyValue(''); setKeyError(null) }}
                    className="px-2 py-1.5 text-xs text-text-muted hover:text-text-primary transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => { setEditingKey(k.provider); setNewKeyValue(''); setKeyError(null) }}
                  className="px-3 py-1.5 text-xs border border-border-default text-text-secondary rounded
                             hover:bg-surface-card hover:text-text-primary transition-colors whitespace-nowrap"
                >
                  {k.has_key ? 'Rotate Key' : 'Add Key'}
                </button>
              )}
            </div>
          ))}
          {editingKey && keyError && (
            <p className="text-xs text-state-error mt-1 px-3">{keyError}</p>
          )}
        </div>
      </section>

      <div className="space-y-6">
      {/* ======= Anthropic OAuth Section ======= */}
      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <SectionTitle icon={ShieldCheck} label="Anthropic OAuth" />
        <p className="text-xs text-text-muted mb-3">
          Connect via your claude.ai subscription (Pro/Max). OAuth tokens auto-refresh every 8 hours.
        </p>
        <div className="flex flex-col gap-3 rounded border border-border-default bg-surface-card-elevated p-3 sm:flex-row sm:items-center">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-text-primary">Anthropic OAuth</span>
              {oauthStatus?.configured ? (
                <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                  oauthStatus.status === 'active'
                    ? 'bg-state-healthy/15 text-state-healthy'
                    : oauthStatus.status === 'expiring'
                    ? 'bg-yellow-500/15 text-yellow-400'
                    : 'bg-state-error/15 text-state-error'
                }`}>
                  {oauthStatus.status === 'active' ? 'CONNECTED' :
                   oauthStatus.status === 'expiring' ? 'EXPIRING' : 'EXPIRED'}
                </span>
              ) : (
                <span className="text-[10px] px-1.5 py-0.5 bg-text-muted/15 text-text-muted rounded font-mono">
                  NOT CONFIGURED
                </span>
              )}
            </div>
            {oauthStatus?.configured && oauthStatus.expires_in_seconds != null && (
              <span className="text-xs text-text-muted mt-1 block">
                Expires in {Math.round(oauthStatus.expires_in_seconds / 60)} min
                {oauthStatus.status === 'active' && ' (auto-refresh enabled)'}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {oauthStatus?.configured ? (
              <>
                <button
                  onClick={handleOAuthSetup}
                  disabled={oauthLoading}
                  className="px-3 py-1.5 text-xs border border-border-default text-text-secondary rounded
                             hover:bg-surface-card hover:text-text-primary transition-colors whitespace-nowrap"
                >
                  Re-authorize
                </button>
                <button
                  onClick={handleOAuthRevoke}
                  className="px-3 py-1.5 text-xs border border-state-error/30 text-state-error rounded
                             hover:bg-state-error/10 transition-colors whitespace-nowrap"
                >
                  Revoke
                </button>
              </>
            ) : (
              <button
                onClick={handleOAuthSetup}
                disabled={oauthLoading}
                className="px-3 py-1.5 text-xs bg-accent-primary/15 text-accent-primary rounded
                           hover:bg-accent-primary/25 transition-colors
                           disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
              >
                {oauthLoading ? 'Opening…' : 'Setup OAuth'}
              </button>
            )}
          </div>
        </div>
      </section>

      {/* ======= OpenAI Codex OAuth Section ======= */}
      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <SectionTitle icon={Zap} label="OpenAI Codex Access" />
        <p className="text-xs text-text-muted mb-3">
          Preferred: sign in on the host with the Codex CLI so `~/.codex/auth.json` is mounted into the container.
          Browser OAuth is available only as a fallback when mounted Codex auth is not present.
        </p>
        <div className="flex flex-col gap-3 rounded border border-border-default bg-surface-card-elevated p-3 sm:flex-row sm:items-center">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-text-primary">OpenAI Codex</span>
              {codexOauthStatus?.configured ? (
                <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                  codexOauthStatus.status === 'active' || codexOauthStatus.status === 'cli_auth'
                    ? 'bg-state-healthy/15 text-state-healthy'
                    : codexOauthStatus.status === 'expiring'
                    ? 'bg-yellow-500/15 text-yellow-400'
                    : 'bg-state-error/15 text-state-error'
                }`}>
                  {codexOauthStatus.status === 'cli_auth' ? 'CLI AUTH' :
                   codexOauthStatus.status === 'active' ? 'CONNECTED' :
                   codexOauthStatus.status === 'expiring' ? 'EXPIRING' : 'EXPIRED'}
                </span>
              ) : (
                <span className="text-[10px] px-1.5 py-0.5 bg-text-muted/15 text-text-muted rounded font-mono">
                  NOT CONFIGURED
                </span>
              )}
            </div>
            {codexOauthStatus?.configured && codexOauthStatus.expires_in_seconds != null && (
              <span className="text-xs text-text-muted mt-1 block">
                Expires in {Math.round(codexOauthStatus.expires_in_seconds / 60)} min
                {codexOauthStatus.status === 'active' && ' (auto-refresh enabled)'}
              </span>
            )}
            {codexOauthStatus?.status === 'cli_auth' && (
              <span className="text-xs text-text-muted mt-1 block">
                Using mounted host Codex auth. Revoke it by signing out on the host machine.
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {codexOauthStatus?.configured ? (
              <>
                <button
                  onClick={handleCodexOAuthSetup}
                  disabled={codexOauthLoading}
                  className="px-3 py-1.5 text-xs border border-border-default text-text-secondary rounded
                             hover:bg-surface-card hover:text-text-primary transition-colors whitespace-nowrap"
                >
                  Re-check
                </button>
                <button
                  onClick={handleCodexOAuthRevoke}
                  className="px-3 py-1.5 text-xs border border-state-error/30 text-state-error rounded
                             hover:bg-state-error/10 transition-colors whitespace-nowrap"
                >
                  Revoke
                </button>
              </>
            ) : (
              <button
                onClick={handleCodexOAuthSetup}
                disabled={codexOauthLoading}
                className="px-3 py-1.5 text-xs bg-accent-primary/15 text-accent-primary rounded
                           hover:bg-accent-primary/25 transition-colors
                           disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
              >
                {codexOauthLoading ? 'Checking…' : 'Check Codex Access'}
              </button>
            )}
          </div>
        </div>
      </section>

      {/* ======= Local OpenAI-Compatible Provider Section ======= */}
      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <SectionTitle icon={ServerCog} label="Local Model Provider" />
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-text-primary">Bring-your-own OpenAI-compatible endpoint</span>
            {localConfig?.base_url ? (
              <StatusBadge tone="healthy">Configured</StatusBadge>
            ) : (
              <StatusBadge tone="warning">Needs Endpoint</StatusBadge>
            )}
            {localConfig?.api_key_configured && (
              <StatusBadge tone="muted">{localConfig.key_preview}</StatusBadge>
            )}
          </div>
          <div className="grid gap-3">
            <label className="block">
              <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-text-muted">Base URL</span>
              <input
                value={localForm.base_url}
                onChange={e => setLocalForm(prev => ({ ...prev, base_url: e.target.value }))}
                placeholder="http://host.docker.internal:11434/v1"
                className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-xs font-mono text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary"
              />
            </label>
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="block">
                <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-text-muted">Fast Model</span>
                <input
                  value={localForm.fast_model}
                  onChange={e => setLocalForm(prev => ({ ...prev, fast_model: e.target.value }))}
                  className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-xs font-mono text-text-primary focus:outline-none focus:ring-1 focus:ring-accent-primary"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-text-muted">Deep Model</span>
                <input
                  value={localForm.deep_model}
                  onChange={e => setLocalForm(prev => ({ ...prev, deep_model: e.target.value }))}
                  className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-xs font-mono text-text-primary focus:outline-none focus:ring-1 focus:ring-accent-primary"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-text-muted">Cache Model</span>
                <input
                  value={localForm.cache_model}
                  onChange={e => setLocalForm(prev => ({ ...prev, cache_model: e.target.value }))}
                  className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-xs font-mono text-text-primary focus:outline-none focus:ring-1 focus:ring-accent-primary"
                />
              </label>
            </div>
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
              <label className="block">
                <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-text-muted">Context Window</span>
                <input
                  type="number"
                  min={1}
                  value={localForm.context_window}
                  onChange={e => setLocalForm(prev => ({ ...prev, context_window: Number(e.target.value) }))}
                  className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-xs font-mono text-text-primary focus:outline-none focus:ring-1 focus:ring-accent-primary"
                />
              </label>
              <label className="flex min-h-9 items-center gap-2 rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-xs text-text-secondary">
                <input
                  type="checkbox"
                  checked={localForm.supports_tools}
                  onChange={e => setLocalForm(prev => ({ ...prev, supports_tools: e.target.checked }))}
                  className="h-4 w-4 accent-accent-primary"
                />
                Tools
              </label>
            </div>
            <label className="block">
              <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-text-muted">Optional API Key</span>
              <input
                type="password"
                value={localForm.api_key}
                onChange={e => setLocalForm(prev => ({ ...prev, api_key: e.target.value }))}
                placeholder={localConfig?.api_key_configured ? 'Leave blank to keep current key' : 'Optional for local servers'}
                className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-xs font-mono text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary"
              />
            </label>
          </div>
          {localError && (
            <p className="text-xs text-state-error">{localError}</p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="min-w-0 truncate text-xs text-text-muted">
              Provider ID: <span className="font-mono">local-openai</span>
            </span>
            <button
              onClick={handleLocalConfigSave}
              disabled={localSaving || !localForm.base_url.trim() || !localForm.fast_model.trim() || !localForm.deep_model.trim()}
              className="inline-flex items-center justify-center rounded bg-accent-primary/15 px-3 py-1.5 text-xs font-medium text-accent-primary transition-colors hover:bg-accent-primary/25 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {localSaving ? 'Saving' : 'Save Local Provider'}
            </button>
          </div>
        </div>
      </section>
      </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Per-Lane Breakdown */}
        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <SectionTitle
            icon={Activity}
            label="Usage by Lane"
            action={usingMonthlyLaneFallback ? <StatusBadge tone="muted">Monthly history</StatusBadge> : undefined}
          />
          {!hasAnyLaneUsage && activeLaneCount === 0 ? (
            <p className="text-sm text-text-muted">Lane usage will appear after the next routed model call.</p>
          ) : (
            <div className="space-y-3">
              {primaryLaneRows.map(({ lane, label, model, usage }) => {
                return (
                  <div key={lane} className="flex flex-col gap-2 rounded border border-border-default bg-surface-card-elevated p-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <span className="block truncate text-sm font-medium text-text-primary" title={lane}>{label}</span>
                      <span className="block truncate text-[11px] font-mono text-text-muted">
                        {model || 'No model assigned'}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-3 text-xs text-text-secondary font-mono sm:justify-end">
                      <span>{usage.calls.toLocaleString()} calls</span>
                      <span>{usage.tokens.toLocaleString()} tokens</span>
                      {usage.cost > 0 && <span>${usage.cost.toFixed(4)}</span>}
                    </div>
                  </div>
                )
              })}
              {nonPrimaryLaneEntries.map(([lane, data]) => {
                const usageRow = readUsageRow(data as Record<string, unknown>)
                const isLegacy = lane === 'legacy_unclassified'
                return (
                  <div key={lane} className="flex flex-col gap-2 rounded border border-border-default bg-surface-base p-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <span className="block truncate text-sm font-medium text-text-secondary" title={lane}>
                        {isLegacy ? 'Pre-lane history' : laneDisplayName(lane)}
                      </span>
                      <span className="block truncate text-[11px] font-mono text-text-muted">
                        {isLegacy ? 'Recorded before lane attribution was enabled' : lane}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-3 text-xs text-text-secondary font-mono sm:justify-end">
                      <span>{usageRow.calls.toLocaleString()} calls</span>
                      <span>{usageRow.tokens.toLocaleString()} tokens</span>
                      {usageRow.cost > 0 && <span>${usageRow.cost.toFixed(4)}</span>}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>

        {/* Per-Model Breakdown */}
        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <SectionTitle icon={ServerCog} label="Usage by Model" />
          {Object.keys(effectiveModelData).length === 0 ? (
            <p className="text-sm text-text-muted">No model data available</p>
          ) : (
            <div className="space-y-3">
              {Object.entries(effectiveModelData).map(([model, data]) => {
                const d = data as Record<string, unknown>
                return (
                  <div key={model} className="flex flex-col gap-2 rounded border border-border-default bg-surface-card-elevated p-3 sm:flex-row sm:items-center sm:justify-between">
                    <span className="min-w-0 truncate text-sm text-text-primary font-mono" title={model}>{model}</span>
                    <div className="flex flex-wrap gap-3 text-xs text-text-secondary font-mono sm:justify-end">
                      <span>{String(d.calls ?? d.requests ?? 0)} calls</span>
                      <span>{Number(d.tokens ?? d.tokens_est ?? 0).toLocaleString()} tokens</span>
                      {(d.estimated_cost ?? d.cost) != null && (
                        <span>${Number(d.estimated_cost ?? d.cost).toFixed(4)}</span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>

        {/* Monthly Summary — Rendered as proper tables */}
        <section className="rounded-lg border border-border-default bg-surface-card p-4 lg:col-span-2">
          <SectionTitle icon={WalletCards} label="Monthly Summary" />
          <div className="flex gap-3 mb-4 flex-wrap items-center">
            {monthly?.available_months?.map((m: string) => (
              <span key={m} className="text-xs font-mono px-2 py-1 bg-accent-primary/15 text-accent-primary rounded">
                {m}
              </span>
            ))}
          </div>

          {!md.month ? (
            <p className="text-sm text-text-muted">No monthly data yet</p>
          ) : (
            <div className="space-y-4">
              {/* Monthly totals */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="p-3 bg-surface-card-elevated rounded-md">
                  <span className="text-[10px] uppercase tracking-wider text-text-muted">Month</span>
                  <p className="text-sm font-mono text-text-primary mt-1">{String(md.month)}</p>
                </div>
                <div className="p-3 bg-surface-card-elevated rounded-md">
                  <span className="text-[10px] uppercase tracking-wider text-text-muted">Requests</span>
                  <p className="text-sm font-mono text-text-primary mt-1">{String(md.total_requests ?? 0)}</p>
                </div>
                <div className="p-3 bg-surface-card-elevated rounded-md">
                  <span className="text-[10px] uppercase tracking-wider text-text-muted">Tokens</span>
                  <p className="text-sm font-mono text-text-primary mt-1">{Number(md.total_tokens ?? 0).toLocaleString()}</p>
                </div>
                <div className="p-3 bg-surface-card-elevated rounded-md">
                  <span className="text-[10px] uppercase tracking-wider text-text-muted">Cost</span>
                  <p className="text-sm font-mono text-text-primary mt-1">${Number(md.total_cost ?? 0).toFixed(4)}</p>
                </div>
              </div>

              {/* By Model table */}
              {Object.keys(byModel).length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-secondary mb-2">By Model</h4>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs font-mono">
                      <thead>
                        <tr className="text-text-muted text-left">
                          <th className="py-1 pr-4">Model</th>
                          <th className="py-1 pr-4 text-right">Requests</th>
                          <th className="py-1 pr-4 text-right">Tokens</th>
                          <th className="py-1 text-right">Cost</th>
                        </tr>
                      </thead>
                      <tbody className="text-text-primary">
                        {Object.entries(byModel).map(([model, d]) => (
                          <tr key={model} className="border-t border-border-default/50">
                            <td className="py-2 pr-4">{model}</td>
                            <td className="py-2 pr-4 text-right">{String(d.requests ?? 0)}</td>
                            <td className="py-2 pr-4 text-right">{Number(d.tokens ?? 0).toLocaleString()}</td>
                            <td className="py-2 text-right">${Number(d.cost ?? 0).toFixed(4)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* By Day table */}
              {Object.keys(byDay).length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-secondary mb-2">By Day</h4>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs font-mono">
                      <thead>
                        <tr className="text-text-muted text-left">
                          <th className="py-1 pr-4">Date</th>
                          <th className="py-1 pr-4 text-right">Requests</th>
                          <th className="py-1 pr-4 text-right">Tokens</th>
                          <th className="py-1 text-right">Cost</th>
                        </tr>
                      </thead>
                      <tbody className="text-text-primary">
                        {Object.entries(byDay).map(([day, d]) => (
                          <tr key={day} className="border-t border-border-default/50">
                            <td className="py-2 pr-4">{day}</td>
                            <td className="py-2 pr-4 text-right">{String(d.requests ?? 0)}</td>
                            <td className="py-2 pr-4 text-right">{Number(d.tokens ?? 0).toLocaleString()}</td>
                            <td className="py-2 text-right">${Number(d.cost ?? 0).toFixed(4)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

/** Collapsible list of discovered models */
function DiscoveredModelsList({ models }: { models: DiscoveredModel[] }) {
  const [expanded, setExpanded] = useState(false)
  const TIER_COLORS: Record<string, string> = {
    fast: 'text-blue-400',
    standard: 'text-text-secondary',
    deep: 'text-purple-400',
  }

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-xs text-text-secondary hover:text-text-primary transition-colors"
      >
        {expanded ? 'Hide' : 'Show'} Available Models ({models.length} discovered)
      </button>
      {expanded && (
        <div className="mt-2 space-y-1">
          {models.map(m => (
            <div key={m.id} className="flex items-center gap-3 px-2 py-1.5 text-xs font-mono rounded bg-surface-card-elevated">
              <span className="text-text-primary truncate max-w-[220px]">{m.display_name || m.id}</span>
              <span className={`${TIER_COLORS[m.capability_tier] || 'text-text-muted'}`}>
                {m.capability_tier}
              </span>
              {m.supports_tools && (
                <span className="text-green-400/70">tools</span>
              )}
              <span className="text-text-muted ml-auto">{formatCtx(m.context_window)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
