import { useState, useEffect } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Database,
  KeyRound,
  Link2,
  LockKeyhole,
  PlugZap,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  Unplug,
} from 'lucide-react'
import { usePolling, usePageTitle } from '@/hooks'
import {
  fetchConnectors,
  enableConnector,
  disableConnector,
  setConnectorBackend,
  storeCredential,
  deleteCredential,
  validateCredentials,
  startGoogleOAuth,
  fetchGoogleOAuthStatus,
  revokeGoogleOAuth,
} from '@/api'
import type { ConnectorInfo, CredentialInfo, GoogleOAuthStatusResponse } from '@/api/connectors'
import { StatusDot, ConfirmDialog, MCPSection } from '@/components'
import { getErrorMessage } from '@/utils/errors'
import { emitWarRoomNotification } from '@/utils/notifications'

// ── Google OAuth vault keys that are managed by the OAuth flow ──
const GOOGLE_OAUTH_KEYS = new Set(['email.gmail_token', 'calendar.google_token'])

// ── Credential Status Helper ────────────────────────────────────
function credentialState(creds: CredentialInfo[]): 'healthy' | 'degraded' | 'inactive' {
  if (creds.length === 0) return 'inactive'
  const allPresent = creds.every(c => c.present)
  const anyPresent = creds.some(c => c.present)
  if (allPresent) return 'healthy'
  if (anyPresent) return 'degraded'
  return 'inactive'
}

function credentialLabel(creds: CredentialInfo[]): string {
  if (creds.length === 0) return 'No credentials'
  const allPresent = creds.every(c => c.present)
  const anyPresent = creds.some(c => c.present)
  if (allPresent) return 'Configured'
  if (anyPresent) return 'Partial'
  return 'Not Configured'
}

function connectorPosture(connector: ConnectorInfo): 'healthy' | 'degraded' | 'inactive' {
  if (!connector.enabled) return 'inactive'
  if (connector.available === false) return 'degraded'
  return credentialState(connector.credentials)
}

function providerLabel(provider: string): string {
  const labels: Record<string, string> = {
    gmail: 'Google Gmail',
    google: 'Google Calendar',
    outlook: 'Microsoft Graph',
    smtp: 'SMTP / IMAP',
    caldav: 'CalDAV',
  }
  return labels[provider] ?? provider
}

function connectorFamily(connector: ConnectorInfo): string {
  if (connector.id === 'email') return 'Email'
  if (connector.id === 'calendar') return 'Calendar'
  if (['teams', 'onedrive', 'sharepoint'].includes(connector.id)) return 'Microsoft Graph'
  if (['slack', 'discord', 'telegram', 'whatsapp', 'sms', 'x'].includes(connector.id)) return 'Messaging'
  if (connector.id === 'shared_workspace') return 'Workspace'
  return 'Custom'
}

function SummaryTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: 'healthy' | 'accent' | 'warning' | 'muted'
}) {
  const tones = {
    healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
    accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
    warning: 'border-state-degraded/30 bg-state-degraded/10 text-state-degraded',
    muted: 'border-border-default bg-surface-card text-text-muted',
  }

  return (
    <div className={`min-w-0 rounded-lg border p-4 ${tones[tone]}`}>
      <div className="text-[10px] font-semibold uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-3 truncate text-2xl font-semibold leading-tight text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

// ── Main Page ────────────────────────────────────────────────────
export function Connectors() {
  usePageTitle('Connectors')
  const { data, refetch } = usePolling({ fetcher: fetchConnectors, interval: 10000 })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [configuring, setConfiguring] = useState<Set<string>>(new Set())
  const [credInputs, setCredInputs] = useState<Record<string, Record<string, string>>>({})
  const [toggling, setToggling] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [validating, setValidating] = useState<string | null>(null)
  const [validationResult, setValidationResult] = useState<Record<string, { valid: boolean; error?: string }>>({})
  const [deleteConfirm, setDeleteConfirm] = useState<{ connectorId: string; vaultKey: string; name: string } | null>(null)
  const [disableConfirm, setDisableConfirm] = useState<string | null>(null)

  // Google OAuth state
  const [googleOAuthStatus, setGoogleOAuthStatus] = useState<GoogleOAuthStatusResponse | null>(null)
  const [googleClientId, setGoogleClientId] = useState('')
  const [googleClientSecret, setGoogleClientSecret] = useState('')
  const [googleOAuthLoading, setGoogleOAuthLoading] = useState(false)
  const [googleOAuthMessage, setGoogleOAuthMessage] = useState('')
  const [revokeConfirm, setRevokeConfirm] = useState(false)

  const reportConnectorError = (error: unknown, fallback: string) => {
    emitWarRoomNotification(getErrorMessage(error, fallback), 'high')
  }

  // Fetch Google OAuth status on mount and periodically
  useEffect(() => {
    const fetchStatus = () => {
      fetchGoogleOAuthStatus()
        .then(res => setGoogleOAuthStatus(res))
        .catch((error) => {
          setGoogleOAuthMessage(getErrorMessage(error, 'Unable to refresh Google OAuth status.'))
        })
    }
    fetchStatus()
    const id = setInterval(fetchStatus, 10000)
    return () => clearInterval(id)
  }, [])

  // Check if a connector uses Google OAuth tokens
  const usesGoogleOAuth = (connector: ConnectorInfo): boolean => {
    return connector.credentials.some(c => GOOGLE_OAUTH_KEYS.has(c.vault_key) && c.type === 'oauth_token')
  }

  const handleGoogleOAuthStart = async () => {
    if (!googleClientId.trim() || !googleClientSecret.trim()) return
    setGoogleOAuthLoading(true)
    setGoogleOAuthMessage('')
    try {
      const res = await startGoogleOAuth(googleClientId.trim(), googleClientSecret.trim())
      if (res.auth_url) {
        window.open(res.auth_url, '_blank', 'noopener,noreferrer')
        setGoogleOAuthMessage('Opened Google consent in new tab. Complete authorization there.')
        // Poll for completion
        const pollId = setInterval(async () => {
          try {
            const status = await fetchGoogleOAuthStatus()
            setGoogleOAuthStatus(status)
            if (status.valid) {
              clearInterval(pollId)
              setGoogleOAuthMessage('Google account connected successfully!')
              setGoogleClientId('')
              setGoogleClientSecret('')
              refetch()
            }
          } catch (error) {
            clearInterval(pollId)
            setGoogleOAuthMessage(getErrorMessage(error, 'Failed to verify Google OAuth completion.'))
          }
        }, 3000)
        // Stop polling after 5 minutes
        setTimeout(() => clearInterval(pollId), 300000)
      }
    } catch (error) {
      setGoogleOAuthMessage(getErrorMessage(error, 'Failed to start Google OAuth flow.'))
    } finally {
      setGoogleOAuthLoading(false)
    }
  }

  const handleGoogleOAuthRevoke = async () => {
    setRevokeConfirm(false)
    try {
      await revokeGoogleOAuth()
      setGoogleOAuthStatus(null)
      setGoogleOAuthMessage('Google OAuth tokens revoked.')
      refetch()
    } catch (error) {
      setGoogleOAuthMessage(getErrorMessage(error, 'Failed to revoke Google tokens.'))
    }
  }

  const connectors = data?.connectors ?? []
  const enabledConnectors = connectors.filter(c => c.enabled).length
  const unavailableConnectors = connectors.filter(c => c.available === false).length
  const missingCredentialConnectors = connectors.filter(c => (
    c.available === false || (c.credentials.length > 0 && credentialState(c.credentials) !== 'healthy')
  )).length
  const googleConnected = Boolean(googleOAuthStatus?.valid)

  const toggleExpand = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleConfigure = (id: string) => {
    setConfiguring(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleToggle = async (connector: ConnectorInfo) => {
    if (connector.enabled) {
      setDisableConfirm(connector.id)
      return
    }
    setToggling(connector.id)
    try {
      await enableConnector(connector.id)
      refetch()
    } catch (error) {
      reportConnectorError(error, `Failed to enable connector ${connector.name}`)
    } finally {
      setToggling(null)
    }
  }

  const doDisable = async (id: string) => {
    setDisableConfirm(null)
    setToggling(id)
    try {
      await disableConnector(id)
      refetch()
    } catch (error) {
      reportConnectorError(error, `Failed to disable connector ${id}`)
    } finally {
      setToggling(null)
    }
  }

  const handleBackendChange = async (id: string, backend: string) => {
    try {
      await setConnectorBackend(id, backend)
      refetch()
    } catch (error) {
      reportConnectorError(error, `Failed to switch connector backend for ${id}`)
    }
  }

  const handleSaveCred = async (connectorId: string, cred: CredentialInfo) => {
    const value = credInputs[connectorId]?.[cred.vault_key]
    if (!value) return
    setSaving(`${connectorId}.${cred.vault_key}`)
    try {
      await storeCredential(connectorId, cred.vault_key, value, cred.type)
      // Clear input after save
      setCredInputs(prev => ({
        ...prev,
        [connectorId]: { ...prev[connectorId], [cred.vault_key]: '' },
      }))
      refetch()
    } catch (error) {
      reportConnectorError(error, `Failed to save credential ${cred.name}`)
    } finally {
      setSaving(null)
    }
  }

  const handleDeleteCred = async () => {
    if (!deleteConfirm) return
    try {
      await deleteCredential(deleteConfirm.connectorId, deleteConfirm.vaultKey)
      refetch()
    } catch (error) {
      reportConnectorError(error, `Failed to delete credential ${deleteConfirm.name}`)
    } finally {
      setDeleteConfirm(null)
    }
  }

  const handleValidate = async (connectorId: string) => {
    setValidating(connectorId)
    try {
      const res = await validateCredentials(connectorId)
      setValidationResult(prev => ({ ...prev, [connectorId]: { valid: res.valid, error: res.error } }))
    } catch {
      setValidationResult(prev => ({ ...prev, [connectorId]: { valid: false, error: 'Request failed' } }))
    } finally {
      setValidating(null)
    }
  }

  const updateCredInput = (connectorId: string, vaultKey: string, value: string) => {
    setCredInputs(prev => ({
      ...prev,
      [connectorId]: { ...prev[connectorId], [vaultKey]: value },
    }))
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-accent-primary">
              <PlugZap className="h-4 w-4" aria-hidden="true" />
              Integration Control
            </div>
            <h1 className="mt-2 text-2xl font-semibold text-text-primary">Connectors</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-text-secondary">
              Enable governed integrations, review data access boundaries, configure masked credentials, and validate connector readiness.
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-3 xl:min-w-[34rem]">
            <div className="rounded border border-border-default bg-surface-card-elevated p-3">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Enabled</div>
              <div className="mt-1 text-lg font-semibold text-text-primary">{enabledConnectors}</div>
            </div>
            <div className={`rounded border p-3 ${missingCredentialConnectors > 0 ? 'border-state-degraded/30 bg-state-degraded/10' : 'border-state-healthy/30 bg-state-healthy/10'}`}>
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Needs Attention</div>
              <div className="mt-1 text-lg font-semibold text-text-primary">{missingCredentialConnectors}</div>
            </div>
            <div className={`rounded border p-3 ${googleConnected ? 'border-state-healthy/30 bg-state-healthy/10' : 'border-border-default bg-surface-card-elevated'}`}>
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Google OAuth</div>
              <div className="mt-1 text-sm font-medium text-text-primary">{googleConnected ? 'Connected' : 'Not connected'}</div>
            </div>
          </div>
        </div>
      </section>

      {/* Summary Bar */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryTile label="Total" value={data?.total ?? 0} detail="Registered connector definitions." tone="accent" />
        <SummaryTile label="Enabled" value={data?.enabled_count ?? 0} detail="Available to governed workflows." tone={enabledConnectors > 0 ? 'healthy' : 'muted'} />
        <SummaryTile label="Configured" value={data?.configured_count ?? 0} detail="Credential requirements satisfied." tone="accent" />
        <SummaryTile label="Attention" value={missingCredentialConnectors} detail={`${unavailableConnectors} unavailable, plus missing credential sets.`} tone={missingCredentialConnectors > 0 ? 'warning' : 'healthy'} />
      </section>

      {/* Connector Cards */}
      {!data ? (
        <section className="rounded-lg border border-border-default bg-surface-card p-6 text-sm text-text-muted">Loading connectors...</section>
      ) : connectors.length === 0 ? (
        <section className="rounded-lg border border-border-default bg-surface-card p-6 text-center">
          <p className="text-sm text-text-muted">No connectors available. Ensure FEATURE_CONNECTORS is enabled.</p>
        </section>
      ) : (
        <div className="space-y-2">
          {connectors.map(connector => {
            const isExpanded = expanded.has(connector.id)
            const isConfiguring = configuring.has(connector.id)
            const cState = credentialState(connector.credentials)
            const posture = connectorPosture(connector)

            return (
              <div key={connector.id} className="overflow-hidden rounded-lg border border-border-default bg-surface-card">
                {/* Header row */}
                <div
                  className="flex cursor-pointer flex-col gap-3 p-4 transition-colors hover:bg-surface-card-elevated sm:flex-row sm:items-center sm:justify-between"
                  onClick={() => toggleExpand(connector.id)}
                >
                  <div className="flex min-w-0 flex-1 items-start gap-3">
                    <ChevronRight className={`mt-0.5 h-4 w-4 shrink-0 text-text-muted transition-transform ${isExpanded ? 'rotate-90' : ''}`} aria-hidden="true" />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium text-text-primary">{connector.name}</span>
                        <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-input text-text-muted">
                          {connector.operation_count} ops
                        </span>
                        <span className="text-[9px] px-1.5 py-0.5 rounded border border-border-default bg-surface-card-elevated text-text-muted">
                          {connectorFamily(connector)}
                        </span>
                        {connector.backend && (
                          <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-accent-primary/15 text-accent-primary">
                            {providerLabel(connector.backend)}
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-text-muted truncate mt-0.5">{connector.description}</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-3 sm:flex-shrink-0 sm:justify-end">
                    <StatusDot state={posture} label={connector.enabled ? 'Enabled' : 'Disabled'} />
                    <StatusDot state={cState} label={credentialLabel(connector.credentials)} />

                    {/* Enable/Disable Toggle */}
                    <button
                      onClick={(e) => { e.stopPropagation(); handleToggle(connector) }}
                      disabled={toggling === connector.id}
                      className={`relative w-11 h-6 rounded-full transition-colors duration-200 flex-shrink-0 ${
                        connector.enabled ? 'bg-state-healthy' : 'bg-surface-input border border-border-default'
                      } ${toggling === connector.id ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                    >
                      <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform duration-200 ${
                        connector.enabled ? 'translate-x-5' : 'translate-x-0'
                      }`} />
                    </button>
                  </div>
                </div>

                {/* Expanded details */}
                {isExpanded && (
                  <div className="space-y-4 border-t border-border-default px-4 pb-4 pt-4">
                    {/* Backend selector */}
                    {connector.available_backends && connector.available_backends.length > 1 && (
                      <div className="flex flex-col gap-2 rounded border border-border-default bg-surface-card-elevated p-3 sm:flex-row sm:items-center">
                        <span className="inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-text-muted">
                          <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
                          Provider
                        </span>
                        <select
                          value={connector.backend || ''}
                          onChange={(e) => { e.stopPropagation(); handleBackendChange(connector.id, e.target.value) }}
                          className="text-xs bg-surface-input border border-border-default rounded px-2 py-1.5 text-text-primary focus:outline-none focus:border-accent-primary"
                        >
                          {connector.available_backends.map(b => (
                            <option key={b} value={b}>{providerLabel(b)}</option>
                          ))}
                        </select>
                      </div>
                    )}

                    {connector.available === false && connector.availability_reason && (
                      <div className="flex items-start gap-2 rounded border border-state-degraded/30 bg-state-degraded/10 p-3 text-[11px] leading-5 text-state-degraded">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        <span>{connector.availability_reason}</span>
                      </div>
                    )}

                    {/* Data access summary */}
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                      <div className="rounded border border-border-default bg-surface-card-elevated p-3">
                        <span className="inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-text-muted">
                          <Database className="h-3.5 w-3.5" aria-hidden="true" />
                          Reads
                        </span>
                        <ul className="mt-1 space-y-0.5">
                          {connector.data_reads.length === 0 ? <li className="text-[11px] text-text-muted">None declared</li> : connector.data_reads.map((r, i) => (
                            <li key={i} className="text-[11px] text-text-secondary">{r}</li>
                          ))}
                        </ul>
                      </div>
                      <div className="rounded border border-border-default bg-surface-card-elevated p-3">
                        <span className="inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-text-muted">
                          <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
                          Writes
                        </span>
                        <ul className="mt-1 space-y-0.5">
                          {connector.data_writes.length === 0 ? <li className="text-[11px] text-text-muted">None declared</li> : connector.data_writes.map((w, i) => (
                            <li key={i} className="text-[11px] text-text-secondary">{w}</li>
                          ))}
                        </ul>
                      </div>
                      <div className="rounded border border-state-healthy/20 bg-state-healthy/5 p-3">
                        <span className="inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-state-healthy">
                          <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                          Does Not Access
                        </span>
                        <ul className="mt-1 space-y-0.5">
                          {connector.does_not_access.length === 0 ? <li className="text-[11px] text-state-healthy/70">None declared</li> : connector.does_not_access.map((d, i) => (
                            <li key={i} className="text-[11px] text-state-healthy/70">{d}</li>
                          ))}
                        </ul>
                      </div>
                    </div>

                    {/* Target domains */}
                    <div className="flex flex-wrap gap-1 mt-1">
                      {connector.target_domains.map(d => (
                        <span key={d} className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-input text-text-muted">
                          {d}
                        </span>
                      ))}
                    </div>

                    {/* Configure credentials button */}
                    <button
                      onClick={(e) => { e.stopPropagation(); toggleConfigure(connector.id) }}
                      className="inline-flex items-center gap-2 rounded border border-border-default bg-surface-input px-3 py-1.5 text-[11px] font-medium text-text-secondary transition-colors hover:bg-surface-card hover:text-text-primary"
                    >
                      <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
                      {isConfiguring ? 'Hide Credentials' : 'Configure Credentials'}
                    </button>

                    {/* Credential form */}
                    {isConfiguring && (
                      <div className="rounded-lg border border-border-default bg-surface-card-elevated p-3">
                        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                          <span className="inline-flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-text-secondary">
                            <LockKeyhole className="h-3.5 w-3.5" aria-hidden="true" />
                            Credentials ({connector.credentials.filter(c => c.present).length}/{connector.credentials.length} configured)
                          </span>
                          <span className="text-[10px] text-text-muted">Secrets are stored in the connector vault.</span>
                        </div>

                        {/* Google OAuth section — shown for connectors using Google OAuth tokens */}
                        {usesGoogleOAuth(connector) && googleOAuthStatus?.feature_enabled && (
                          <div className="mt-3 rounded-lg border border-border-default bg-surface-input/50 p-3">
                            <div className="flex items-center justify-between mb-2">
                              <span className="inline-flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-text-secondary">
                                <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
                                Google OAuth
                              </span>
                              {googleOAuthStatus?.valid && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded bg-state-healthy/15 text-state-healthy">
                                  CONNECTED
                                </span>
                              )}
                              {googleOAuthStatus?.status === 'expired' && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded bg-state-error/15 text-state-error">
                                  EXPIRED
                                </span>
                              )}
                              {googleOAuthStatus?.status === 'expiring_soon' && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded bg-state-degraded/15 text-state-degraded">
                                  EXPIRING
                                </span>
                              )}
                            </div>

                            {googleOAuthStatus?.valid ? (
                              <div>
                                <p className="text-[11px] text-text-secondary mb-2">
                                  Google account connected. Token auto-refreshes before expiry.
                                  {googleOAuthStatus.expires_in_seconds != null && (
                                    <span className="text-text-muted ml-1">
                                      (expires in {Math.round(googleOAuthStatus.expires_in_seconds / 60)} min)
                                    </span>
                                  )}
                                </p>
                                <button
                                  onClick={() => setRevokeConfirm(true)}
                                  className="inline-flex items-center gap-2 rounded bg-state-error/10 px-3 py-1.5 text-[11px] font-medium text-state-error transition-colors hover:bg-state-error/20"
                                >
                                  <Unplug className="h-3.5 w-3.5" aria-hidden="true" />
                                  Disconnect Google
                                </button>
                              </div>
                            ) : (
                              <div>
                                <p className="text-[11px] text-text-muted mb-2">
                                  Enter your Google Cloud OAuth credentials to connect Gmail and Calendar.
                                </p>
                                <div className="space-y-2">
                                  <input
                                    type="text"
                                    placeholder="Client ID (e.g. 123456.apps.googleusercontent.com)"
                                    value={googleClientId}
                                    onChange={(e) => setGoogleClientId(e.target.value)}
                                    className="w-full bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
                                  />
                                  <input
                                    type="password"
                                    placeholder="Client Secret"
                                    value={googleClientSecret}
                                    onChange={(e) => setGoogleClientSecret(e.target.value)}
                                    className="w-full bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
                                  />
                                  <button
                                    onClick={handleGoogleOAuthStart}
                                    disabled={googleOAuthLoading || !googleClientId.trim() || !googleClientSecret.trim()}
                                    className={`inline-flex items-center gap-2 rounded px-3 py-1.5 text-[11px] font-medium transition-colors ${
                                      googleClientId.trim() && googleClientSecret.trim()
                                        ? 'bg-accent-primary text-white hover:bg-accent-primary/80'
                                        : 'bg-surface-input text-text-muted cursor-not-allowed'
                                    } disabled:opacity-50`}
                                  >
                                    <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
                                    {googleOAuthLoading ? 'Opening...' : 'Authorize with Google'}
                                  </button>
                                </div>
                                {googleOAuthMessage && (
                                  <p className={`text-[11px] mt-2 ${
                                    googleOAuthMessage.includes('successfully') ? 'text-state-healthy' :
                                    googleOAuthMessage.includes('Failed') ? 'text-state-error' :
                                    'text-text-muted'
                                  }`}>
                                    {googleOAuthMessage}
                                  </p>
                                )}
                              </div>
                            )}
                          </div>
                        )}

                        {connector.credentials.map(cred => {
                          // Skip manual input for Google OAuth-managed credentials when OAuth is connected
                          if (GOOGLE_OAUTH_KEYS.has(cred.vault_key) && cred.type === 'oauth_token' && googleOAuthStatus?.feature_enabled) {
                            return null
                          }
                          return (
                          <div key={cred.vault_key} className="mt-3 rounded border border-border-default bg-surface-card p-3">
                            <div className="mb-2 flex flex-wrap items-center gap-2">
                              <span className="text-[11px] font-medium text-text-primary">{cred.name}</span>
                              <span className={`text-[9px] px-1.5 py-0.5 rounded ${
                                cred.present ? 'bg-state-healthy/15 text-state-healthy' : 'bg-state-error/15 text-state-error'
                              }`}>
                                {cred.present ? 'stored' : 'missing'}
                              </span>
                              <span className="text-[9px] px-1.5 py-0.5 rounded bg-surface-input text-text-muted">{cred.type}</span>
                              {cred.required && <span className="text-[9px] text-state-degraded">required</span>}
                            </div>
                            <div className="flex flex-col gap-2 sm:flex-row">
                              <input
                                type={cred.type === 'config' ? 'text' : 'password'}
                                placeholder={cred.present ? (cred.type === 'config' ? 'Current value stored' : '••••••••') : `Enter ${cred.type === 'config' ? 'value' : cred.type}...`}
                                value={credInputs[connector.id]?.[cred.vault_key] || ''}
                                onChange={(e) => updateCredInput(connector.id, cred.vault_key, e.target.value)}
                                className="flex-1 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
                              />
                              <button
                                onClick={() => handleSaveCred(connector.id, cred)}
                                disabled={saving === `${connector.id}.${cred.vault_key}` || !credInputs[connector.id]?.[cred.vault_key]}
                                className={`inline-flex items-center justify-center gap-2 rounded px-3 py-1.5 text-[11px] font-medium transition-colors ${
                                  credInputs[connector.id]?.[cred.vault_key]
                                    ? 'bg-accent-primary text-white hover:bg-accent-primary/80'
                                    : 'bg-surface-input text-text-muted cursor-not-allowed'
                                }`}
                              >
                                <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                                {saving === `${connector.id}.${cred.vault_key}` ? 'Saving...' : 'Save'}
                              </button>
                              {cred.present && (
                                <button
                                  onClick={() => setDeleteConfirm({ connectorId: connector.id, vaultKey: cred.vault_key, name: cred.name })}
                                  className="inline-flex items-center justify-center gap-2 rounded bg-state-error/10 px-3 py-1.5 text-[11px] font-medium text-state-error transition-colors hover:bg-state-error/20"
                                >
                                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                                  Delete
                                </button>
                              )}
                            </div>
                            {cred.scopes.length > 0 && (
                              <div className="flex flex-wrap gap-1 mt-1">
                                {cred.scopes.map(s => (
                                  <span key={s} className="text-[8px] font-mono px-1 py-0.5 rounded bg-surface-input text-text-muted">
                                    {s}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                          )
                        })}

                        {/* Test Connection */}
                        <div className="mt-4 flex flex-col gap-2 border-t border-border-default/50 pt-3 sm:flex-row sm:items-center">
                          <button
                            onClick={() => handleValidate(connector.id)}
                            disabled={validating === connector.id}
                            className="inline-flex items-center justify-center gap-2 rounded bg-accent-primary px-3 py-1.5 text-[11px] font-medium text-white transition-colors hover:bg-accent-primary/80 disabled:opacity-50"
                          >
                            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                            {validating === connector.id ? 'Testing...' : 'Test Connection'}
                          </button>
                          {validationResult[connector.id] != null && (
                            <span className={`text-[11px] ${
                              validationResult[connector.id]?.valid ? 'text-state-healthy' : 'text-state-error'
                            }`}>
                              {validationResult[connector.id]?.valid ? 'Connection OK' : validationResult[connector.id]?.error || 'Validation failed'}
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* MCP Servers Section — governed MCP tool proxy */}
      <MCPSection />

      {/* Confirm Dialogs */}
      <ConfirmDialog
        open={disableConfirm !== null}
        title="Disable Connector"
        description={`This will disable the ${disableConfirm ?? ''} connector and unregister it from the runtime. Stored credentials will be preserved. Continue?`}
        variant="destructive"
        confirmLabel="Disable"
        onConfirm={() => disableConfirm && doDisable(disableConfirm)}
        onCancel={() => setDisableConfirm(null)}
      />

      <ConfirmDialog
        open={deleteConfirm !== null}
        title="Delete Credential"
        description={`This will permanently remove the ${deleteConfirm?.name ?? ''} credential from the vault. The connector will need to be reconfigured. Continue?`}
        variant="destructive"
        confirmLabel="Delete"
        onConfirm={handleDeleteCred}
        onCancel={() => setDeleteConfirm(null)}
      />

      <ConfirmDialog
        open={revokeConfirm}
        title="Disconnect Google Account"
        description="This will revoke the Google OAuth tokens and disconnect both Gmail and Calendar. You will need to re-authorize to use these connectors again. Continue?"
        variant="destructive"
        confirmLabel="Disconnect"
        onConfirm={handleGoogleOAuthRevoke}
        onCancel={() => setRevokeConfirm(false)}
      />
    </div>
  )
}
