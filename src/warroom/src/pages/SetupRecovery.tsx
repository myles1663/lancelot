import { useState, useRef, useEffect, useCallback } from 'react'
import { usePolling, usePageTitle } from '@/hooks'
import {
  fetchOnboardingStatus,
  fetchModelUsagePolicy,
  sendOnboardingCommand,
  onboardingBack,
  onboardingRestartStep,
  onboardingResendCode,
  onboardingReset,
  fetchSystemInfo,
  restartContainer,
  shutdownContainer,
  fetchLogs,
  fetchVaultMasked,
  fetchVaultStatus,
  deleteVaultKey,
  fetchTokens,
  revokeToken,
  clearReceipts,
  resetUsage,
  reloadConfig,
  updateModelUsagePolicy,
  exportBackup,
  factoryReset,
  purgeMemory,
  resetConnectorVault,
  resetFlags,
} from '@/api'
import { fetchReceiptStats } from '@/api/receipts'
import { MetricCard, StatusDot, ConfirmDialog, EmptyState } from '@/components'
import { formatTimestamp, formatUptime } from '@/utils/dateFormat'
import { getErrorMessage } from '@/utils/errors'
import { emitWarRoomNotification } from '@/utils/notifications'
import type {
  SystemInfoResponse,
  VaultMaskedEntry,
  VaultStatusResponse,
  ExecutionToken,
  LocalModelRoleStatus,
  ModelUsagePolicyResponse,
} from '@/types/api'

// â”€â”€ Tab definitions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const TABS = [
  { id: 'system', label: 'System' },
  { id: 'data', label: 'Data' },
  { id: 'logs', label: 'Logs & Config' },
  { id: 'danger', label: 'Danger Zone' },
] as const

type TabId = (typeof TABS)[number]['id']

function normalizeLocalModelRoles(
  rolesPayload: ModelUsagePolicyResponse['local_model_roles'],
): Array<[string, LocalModelRoleStatus]> {
  if (!rolesPayload) return []
  if ('roles' in rolesPayload && rolesPayload.roles) {
    return Object.entries(rolesPayload.roles)
  }
  return Object.entries(rolesPayload as Record<string, LocalModelRoleStatus>)
}

// â”€â”€ Section wrapper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
      <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
        {title}
      </h3>
      {children}
    </section>
  )
}

function getVaultStatusState(
  status: string,
): 'healthy' | 'degraded' | 'error' | 'inactive' {
  switch (status) {
    case 'ready':
      return 'healthy'
    case 'empty':
    case 'configured':
      return 'inactive'
    case 'key_mismatch':
    case 'decryption_failed':
    case 'missing_key':
    case 'ephemeral_key':
      return 'error'
    default:
      return 'degraded'
  }
}

// â”€â”€ Button helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function ActionButton({
  label,
  onClick,
  loading,
  variant = 'default',
}: {
  label: string
  onClick: () => void
  loading?: boolean
  variant?: 'default' | 'destructive' | 'warning'
}) {
  const base = 'px-3 py-2 text-sm rounded-md transition-colors disabled:opacity-50'
  const styles = {
    default:
      'bg-surface-input border border-border-default text-text-secondary hover:text-text-primary hover:bg-surface-card-elevated',
    destructive:
      'bg-surface-input border border-state-error/30 text-state-error hover:bg-state-error/10',
    warning:
      'bg-surface-input border border-state-degraded/30 text-state-degraded hover:bg-state-degraded/10',
  }
  return (
    <button onClick={onClick} disabled={loading} className={`${base} ${styles[variant]}`}>
      {loading ? 'Working...' : label}
    </button>
  )
}

// â”€â”€ Main Component â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export function SetupRecovery() {
  usePageTitle('Setup & Recovery')
  const [tab, setTab] = useState<TabId>('system')

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-4">Setup & Recovery</h2>

      {/* Tab Navigation */}
      <div className="flex gap-1 mb-6 bg-surface-input rounded-lg p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex-1 px-3 py-2 text-sm font-medium rounded-md transition-colors ${
              tab === t.id
                ? t.id === 'danger'
                  ? 'bg-state-error/10 text-state-error border border-state-error/20'
                  : 'bg-surface-card text-text-primary shadow-sm'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'system' && <SystemTab />}
      {tab === 'data' && <DataTab />}
      {tab === 'logs' && <LogsConfigTab />}
      {tab === 'danger' && <DangerTab />}
    </div>
  )
}

// ================================================================
// TAB 1: System
// ================================================================

function SystemTab() {
  const { data: sysInfo } = usePolling<SystemInfoResponse>({
    fetcher: fetchSystemInfo,
    interval: 10000,
  })
  const { data: onboarding, refetch: refetchOb } = usePolling({
    fetcher: fetchOnboardingStatus,
    interval: 10000,
  })
  const [cmdResult, setCmdResult] = useState<string | null>(null)
  const [confirmAction, setConfirmAction] = useState<'restart' | 'shutdown' | null>(null)
  const [modelPolicy, setModelPolicy] = useState<ModelUsagePolicyResponse | null>(null)
  const [localExecutionMode, setLocalExecutionMode] = useState('low_risk_only')
  const [frontierScrubMode, setFrontierScrubMode] = useState('required')
  const [policySaving, setPolicySaving] = useState(false)
  const [policyResult, setPolicyResult] = useState<string | null>(null)
  const localModelRoles = normalizeLocalModelRoles(modelPolicy?.local_model_roles)

  const loadModelPolicy = useCallback(async () => {
    const policy = await fetchModelUsagePolicy()
    setModelPolicy(policy)
    setLocalExecutionMode(policy.local_execution_mode)
    setFrontierScrubMode(policy.frontier_scrub_mode)
  }, [])

  useEffect(() => {
    loadModelPolicy().catch((error) => {
      const message = getErrorMessage(error, 'Failed to load model usage policy.')
      setPolicyResult(message)
      emitWarRoomNotification(message, 'normal')
    })
  }, [loadModelPolicy])

  const runCommand = async (fn: () => Promise<{ response: string }>) => {
    const res = await fn()
    setCmdResult(res.response)
    refetchOb()
  }

  const handleContainerAction = async () => {
    try {
      if (confirmAction === 'restart') await restartContainer()
      if (confirmAction === 'shutdown') await shutdownContainer()
    } catch {
      // Expected â€” connection will drop
    } finally {
      setConfirmAction(null)
    }
  }

  const handleSaveModelPolicy = async () => {
    setPolicySaving(true)
    setPolicyResult(null)
    try {
      const policy = await updateModelUsagePolicy({
        local_execution_mode: localExecutionMode,
        frontier_scrub_mode: frontierScrubMode,
      })
      setModelPolicy(policy)
      setPolicyResult('Model usage policy saved.')
    } catch {
      setPolicyResult('Failed to save model usage policy.')
    } finally {
      setPolicySaving(false)
    }
  }

  return (
    <>
      {/* System Info */}
      <Section title="System Info">
        {!sysInfo ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <MetricCard label="Version" value={`v${sysInfo.version}`} />
            <MetricCard label="Uptime" value={formatUptime(sysInfo.uptime_seconds)} />
            <MetricCard label="Python" value={sysInfo.python_version} />
            <MetricCard
              label="Disk Usage"
              value={`${sysInfo.data_dir.used_mb} / ${sysInfo.data_dir.total_mb} MB`}
            />
          </div>
        )}
      </Section>

      {/* Container Controls */}
      <Section title="Container Controls">
        <div className="flex flex-wrap gap-3">
          <ActionButton
            label="Restart Container"
            variant="warning"
            onClick={() => setConfirmAction('restart')}
          />
          <ActionButton
            label="Shutdown Container"
            variant="destructive"
            onClick={() => setConfirmAction('shutdown')}
          />
        </div>
        <p className="text-xs text-text-muted mt-2">
          Restart uses exit code 0 (Docker auto-restarts). Shutdown uses exit code 1 (stays stopped).
        </p>
      </Section>

      {/* Onboarding Status */}
      <Section title="Onboarding Status">
        {!onboarding ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">State</span>
                <div className="mt-1">
                  <StatusDot
                    state={onboarding.is_ready ? 'healthy' : 'degraded'}
                    label={onboarding.state}
                  />
                </div>
              </div>
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Provider</span>
                <p className="text-sm font-mono text-text-primary mt-1">
                  {onboarding.flagship_provider || 'None'}
                </p>
              </div>
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Mode</span>
                <p className="text-sm font-mono text-text-primary mt-1">
                  {(onboarding.provider_mode || 'sdk').toUpperCase()}
                </p>
              </div>
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">
                  Credentials
                </span>
                <p className="text-sm font-mono text-text-primary mt-1">
                  {onboarding.credential_status}
                </p>
              </div>
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">
                  Local Model Install
                </span>
                <p className="text-sm font-mono text-text-primary mt-1">
                  {onboarding.local_model_status}
                </p>
              </div>
            </div>
            <div className="mt-4 grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="p-3 bg-surface-card-elevated rounded-md">
                <span className="text-[10px] uppercase tracking-wider text-text-muted">
                  Runtime State
                </span>
                <div className="mt-2">
                  <StatusDot
                    state={
                      onboarding.local_model_runtime_ready
                        ? 'healthy'
                        : onboarding.local_model_runtime_loaded
                          ? 'degraded'
                          : 'inactive'
                    }
                    label={onboarding.local_model_runtime_status || 'unknown'}
                  />
                </div>
              </div>
              <div className="p-3 bg-surface-card-elevated rounded-md">
                <span className="text-[10px] uppercase tracking-wider text-text-muted">
                  Last Verified
                </span>
                <p className="text-xs font-mono text-text-primary mt-2">
                  {onboarding.local_model_last_verified_at
                    ? formatTimestamp(onboarding.local_model_last_verified_at)
                    : '--'}
                </p>
              </div>
              <div className="p-3 bg-surface-card-elevated rounded-md">
                <span className="text-[10px] uppercase tracking-wider text-text-muted">
                  Last Smoke
                </span>
                <p className="text-xs font-mono text-text-primary mt-2">
                  {onboarding.local_model_last_smoke_elapsed_ms != null
                    ? `${onboarding.local_model_last_smoke_elapsed_ms} ms`
                    : '--'}
                </p>
              </div>
              <div className="p-3 bg-surface-card-elevated rounded-md">
                <span className="text-[10px] uppercase tracking-wider text-text-muted">
                  Failure Count
                </span>
                <p className="text-sm font-mono text-text-primary mt-2">
                  {onboarding.local_model_consecutive_failures ?? 0}
                </p>
              </div>
            </div>
            {onboarding.local_model_last_error && (
              <div className="mt-4 p-3 bg-state-error/10 border border-state-error/30 rounded">
                <span className="text-xs font-semibold text-state-error">
                  Local runtime not ready
                </span>
                <p className="text-xs text-text-secondary mt-1">
                  {onboarding.local_model_last_error}
                </p>
              </div>
            )}
          </>
        )}
        {onboarding?.cooldown_active && (
          <div className="mt-4 p-3 bg-state-degraded/10 border border-state-degraded/30 rounded">
            <span className="text-xs font-semibold text-state-degraded">
              Cooldown Active â€” {Math.round(onboarding.cooldown_remaining)}s remaining
            </span>
            {onboarding.last_error && (
              <p className="text-xs text-text-secondary mt-1">{onboarding.last_error}</p>
            )}
          </div>
        )}
      </Section>

      <Section title="Local Model Usage Policy">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-[10px] uppercase tracking-wider text-text-muted mb-1">
              Local Execution
            </label>
            <select
              value={localExecutionMode}
              onChange={(e) => setLocalExecutionMode(e.target.value)}
              className="w-full text-sm bg-surface-input border border-border-default rounded-md px-3 py-2 text-text-primary"
            >
              <option value="low_risk_only">Low-Risk Only</option>
              <option value="disabled">Disabled</option>
            </select>
            <p className="text-xs text-text-muted mt-2">
              Uses the installed local model only for low-risk utility tasks to save frontier tokens. It is not a replacement for frontier reasoning.
            </p>
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wider text-text-muted mb-1">
              Frontier Scrubbing
            </label>
            <select
              value={frontierScrubMode}
              onChange={(e) => setFrontierScrubMode(e.target.value)}
              className="w-full text-sm bg-surface-input border border-border-default rounded-md px-3 py-2 text-text-primary"
            >
              <option value="required">Required</option>
              <option value="preferred">Preferred With Fallback</option>
              <option value="disabled">Disabled</option>
            </select>
            <p className="text-xs text-text-muted mt-2">
              Controls whether frontier-bound content must be scrubbed locally first or may fall back to direct frontier egress.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
          <div className="p-3 bg-surface-card-elevated rounded-md">
            <span className="text-[10px] uppercase tracking-wider text-text-muted">Execution Available</span>
            <p className="text-sm font-mono text-text-primary mt-1">
              {modelPolicy?.local_execution_available ? 'yes' : 'no'}
            </p>
          </div>
          <div className="p-3 bg-surface-card-elevated rounded-md">
            <span className="text-[10px] uppercase tracking-wider text-text-muted">Scrub Available</span>
            <p className="text-sm font-mono text-text-primary mt-1">
              {modelPolicy?.local_scrub_available ? 'yes' : 'no'}
            </p>
          </div>
          <div className="p-3 bg-surface-card-elevated rounded-md">
            <span className="text-[10px] uppercase tracking-wider text-text-muted">Fallback Count</span>
            <p className="text-sm font-mono text-text-primary mt-1">
              {modelPolicy?.frontier_scrub_fallback_count ?? 0}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mt-4">
          <div className="p-3 bg-surface-card-elevated rounded-md">
            <span className="text-[10px] uppercase tracking-wider text-text-muted">Loaded</span>
            <p className="text-sm font-mono text-text-primary mt-1">
              {modelPolicy?.local_model_loaded ? 'yes' : 'no'}
            </p>
          </div>
          <div className="p-3 bg-surface-card-elevated rounded-md">
            <span className="text-[10px] uppercase tracking-wider text-text-muted">Ready</span>
            <p className="text-sm font-mono text-text-primary mt-1">
              {modelPolicy?.local_model_ready ? 'yes' : 'no'}
            </p>
          </div>
          <div className="p-3 bg-surface-card-elevated rounded-md">
            <span className="text-[10px] uppercase tracking-wider text-text-muted">Last Verified</span>
            <p className="text-xs font-mono text-text-primary mt-1">
              {modelPolicy?.local_model_last_verified_at
                ? formatTimestamp(modelPolicy.local_model_last_verified_at)
                : '--'}
            </p>
          </div>
          <div className="p-3 bg-surface-card-elevated rounded-md">
            <span className="text-[10px] uppercase tracking-wider text-text-muted">Failure Count</span>
            <p className="text-sm font-mono text-text-primary mt-1">
              {modelPolicy?.local_model_consecutive_failures ?? 0}
            </p>
          </div>
        </div>

        {localModelRoles.length > 0 && (
          <div className="mt-4 border border-border-default rounded-md overflow-hidden">
            <div className="grid grid-cols-[1.1fr_0.8fr_0.5fr_1fr_1.4fr] gap-3 px-3 py-2 bg-surface-input text-[10px] uppercase tracking-wider text-text-muted">
              <span>Role</span>
              <span>Status</span>
              <span>Priority</span>
              <span>Model</span>
              <span>Endpoint</span>
            </div>
            {localModelRoles.map(([role, status]) => (
              <div
                key={role}
                className="grid grid-cols-[1.1fr_0.8fr_0.5fr_1fr_1.4fr] gap-3 px-3 py-2 border-t border-border-default text-xs"
              >
                <span className="font-mono text-text-primary truncate">{role}</span>
                <span className={status.ready ? 'text-state-healthy' : 'text-state-degraded'}>
                  {status.status || (status.ready ? 'ready' : 'unavailable')}
                </span>
                <span className="font-mono text-text-secondary">{status.priority ?? '--'}</span>
                <span className="font-mono text-text-muted truncate">{status.model || '--'}</span>
                <span className="font-mono text-text-muted truncate">{status.base_url || '--'}</span>
              </div>
            ))}
          </div>
        )}

        <div className="mt-3">
          <p className="text-xs text-text-muted">
            {modelPolicy?.availability_reason || 'Local model status unavailable.'}
          </p>
          {modelPolicy?.local_model_last_error && (
            <p className="text-xs text-state-error mt-1">
              Last readiness error: {modelPolicy.local_model_last_error}
            </p>
          )}
          {modelPolicy?.frontier_scrub_fallback_active && (
            <p className="text-xs text-state-degraded mt-1">
              Frontier scrub fallback active: {modelPolicy.last_frontier_scrub_fallback_reason || 'local scrubbing unavailable'}
            </p>
          )}
        </div>

        <div className="flex items-center justify-between mt-4">
          <p className="text-xs text-text-muted">
            Privacy fallback can never be silent. Preferred mode records and surfaces every frontier scrub fallback.
          </p>
          <ActionButton
            label="Save Policy"
            onClick={handleSaveModelPolicy}
            loading={policySaving}
          />
        </div>
        {policyResult && (
          <p className="text-xs text-text-secondary mt-3">{policyResult}</p>
        )}
      </Section>

      {/* Recovery Commands */}
      <Section title="Recovery Commands">
        <div className="flex flex-wrap gap-2">
          <ActionButton
            label="Check Status"
            onClick={() => runCommand(() => sendOnboardingCommand('STATUS'))}
          />
          <ActionButton label="Go Back" onClick={() => runCommand(onboardingBack)} />
          <ActionButton label="Restart Step" onClick={() => runCommand(onboardingRestartStep)} />
          <ActionButton label="Resend Code" onClick={() => runCommand(onboardingResendCode)} />
        </div>
      </Section>

      {/* Command Result */}
      {cmdResult && (
        <Section title="Command Result">
          <pre className="text-sm font-mono text-text-primary bg-surface-input rounded p-3 whitespace-pre-wrap">
            {cmdResult}
          </pre>
        </Section>
      )}

      {/* Confirm Dialogs */}
      <ConfirmDialog
        open={confirmAction === 'restart'}
        title="Restart Container"
        description="This will gracefully stop all subsystems and restart the Docker container. The system will be back online in a few seconds."
        variant="default"
        confirmLabel="Restart"
        onConfirm={handleContainerAction}
        onCancel={() => setConfirmAction(null)}
      />
      <ConfirmDialog
        open={confirmAction === 'shutdown'}
        title="Shutdown Container"
        description="This will stop the container and it will NOT auto-restart. You will need to manually start it again from Docker."
        variant="destructive"
        confirmLabel="Shut Down"
        onConfirm={handleContainerAction}
        onCancel={() => setConfirmAction(null)}
      />
    </>
  )
}

// ================================================================
// TAB 2: Data
// ================================================================

function DataTab() {
  const { data: vaultStatus, refetch: refetchVaultStatus } = usePolling<VaultStatusResponse>({
    fetcher: fetchVaultStatus,
    interval: 30000,
  })
  const { data: vaultData, refetch: refetchVault } = usePolling({
    fetcher: fetchVaultMasked,
    interval: 30000,
  })
  const { data: tokensData, refetch: refetchTokens } = usePolling({
    fetcher: () => fetchTokens(50),
    interval: 15000,
  })
  const { data: receiptStats, refetch: refetchReceipts } = usePolling({
    fetcher: fetchReceiptStats,
    interval: 30000,
  })

  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [revokeConfirm, setRevokeConfirm] = useState<string | null>(null)
  const [clearReceiptsConfirm, setClearReceiptsConfirm] = useState(false)

  const handleDeleteKey = async () => {
    if (!deleteConfirm) return
    try {
      await deleteVaultKey(deleteConfirm)
      refetchVault()
      refetchVaultStatus()
    } finally {
      setDeleteConfirm(null)
    }
  }

  const handleRevokeToken = async () => {
    if (!revokeConfirm) return
    try {
      await revokeToken(revokeConfirm)
      refetchTokens()
    } finally {
      setRevokeConfirm(null)
    }
  }

  const handleClearReceipts = async () => {
    try {
      await clearReceipts()
      refetchReceipts()
    } finally {
      setClearReceiptsConfirm(false)
    }
  }

  const handleResetUsage = async () => {
    await resetUsage()
  }

  return (
    <>
      <Section title="Connector Vault Health">
        {!vaultStatus ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <StatusDot
                state={getVaultStatusState(vaultStatus.status)}
                label={vaultStatus.status.replace(/_/g, ' ')}
              />
              <span className="text-sm text-text-secondary">{vaultStatus.message}</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              <div className="p-3 bg-surface-card-elevated rounded-md">
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Key Source</span>
                <p className="text-sm font-mono text-text-primary mt-1">
                  {vaultStatus.key_origin} / {vaultStatus.key_source}
                </p>
              </div>
              <div className="p-3 bg-surface-card-elevated rounded-md">
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Key Id</span>
                <p className="text-sm font-mono text-text-primary mt-1">
                  {vaultStatus.key_id ? vaultStatus.key_id.slice(0, 16) : '--'}
                </p>
              </div>
              <div className="p-3 bg-surface-card-elevated rounded-md">
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Vault Files</span>
                <p className="text-sm font-mono text-text-primary mt-1">
                  primary={vaultStatus.has_primary ? 'yes' : 'no'} backup={vaultStatus.has_backup ? 'yes' : 'no'}
                </p>
              </div>
              <div className="p-3 bg-surface-card-elevated rounded-md">
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Entries</span>
                <p className="text-sm font-mono text-text-primary mt-1">
                  {vaultStatus.available ? vaultStatus.entry_count : '--'}
                </p>
              </div>
            </div>
            <div className="mt-4 text-[11px] text-text-muted space-y-1">
              <p className="font-mono">Primary: {vaultStatus.primary_path}</p>
              <p className="font-mono">Backup: {vaultStatus.backup_path}</p>
              <p className="font-mono">Metadata: {vaultStatus.metadata_path}</p>
              <p className="font-mono">Reset Archives: {vaultStatus.reset_backups_path}</p>
            </div>
            {(vaultStatus.last_error || vaultStatus.suspected_key_mismatch) && (
              <div className="mt-4 p-3 bg-state-error/10 border border-state-error/30 rounded">
                <span className="text-xs font-semibold text-state-error">
                  Operator Attention Required
                </span>
                <p className="text-xs text-text-secondary mt-1">
                  {vaultStatus.suspected_key_mismatch
                    ? 'The configured vault key does not match the key id recorded for the encrypted vault.'
                    : vaultStatus.last_error}
                </p>
              </div>
            )}
          </>
        )}
      </Section>

      {/* Vault Credentials */}
      <Section title="Vault Credentials">
        {!vaultData || !vaultStatus ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : !vaultStatus.available ? (
          <EmptyState
            title="Vault Unavailable"
            description={vaultStatus.message}
          />
        ) : vaultData.keys.length === 0 ? (
          <EmptyState title="No Credentials" description="No credentials stored in the vault." />
        ) : (
          <div className="space-y-2">
            {vaultData.keys.map((entry: VaultMaskedEntry) => (
              <div
                key={entry.key}
                className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md"
              >
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-mono text-text-primary">{entry.key}</span>
                  <div className="flex gap-3 mt-0.5">
                    <span className="text-[10px] text-text-muted">Type: {entry.type}</span>
                    <span className="text-[10px] font-mono text-text-muted">{entry.masked_value}</span>
                    {entry.created_at && (
                      <span className="text-[10px] text-text-muted">
                        Created: {formatTimestamp(entry.created_at)}
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => setDeleteConfirm(entry.key)}
                  className="px-2 py-1 text-xs text-state-error hover:bg-state-error/10 rounded transition-colors"
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
        <p className="text-[10px] text-text-muted mt-2">
          Values are masked (first 4 + last 4 characters shown). Full values are never displayed.
        </p>
      </Section>

      {/* Execution Tokens */}
      <Section title="Execution Tokens">
        {!tokensData ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : tokensData.tokens.length === 0 ? (
          <EmptyState title="No Tokens" description="No execution tokens found." />
        ) : (
          <div className="space-y-2">
            {tokensData.tokens.map((token: ExecutionToken) => (
              <div
                key={token.id}
                className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md"
              >
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-mono text-text-primary truncate">{token.id}</span>
                  <div className="flex gap-3 mt-0.5">
                    <StatusDot
                      state={token.status === 'active' ? 'healthy' : 'inactive'}
                      label={token.status}
                    />
                  </div>
                </div>
                {token.status === 'active' && (
                  <button
                    onClick={() => setRevokeConfirm(token.id)}
                    className="px-2 py-1 text-xs text-state-degraded hover:bg-state-degraded/10 rounded transition-colors"
                  >
                    Revoke
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Receipt Management */}
      <Section title="Receipt Management">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-text-primary">
              Total Receipts:{' '}
              <span className="font-mono">
                {receiptStats?.stats?.total_receipts ?? '--'}
              </span>
            </p>
          </div>
          <ActionButton
            label="Clear All Receipts"
            variant="destructive"
            onClick={() => setClearReceiptsConfirm(true)}
          />
        </div>
      </Section>

      {/* Usage Counters */}
      <Section title="Usage Counters">
        <div className="flex items-center justify-between">
          <p className="text-sm text-text-secondary">Reset in-memory usage counters for a fresh tracking period.</p>
          <ActionButton label="Reset Usage" variant="warning" onClick={handleResetUsage} />
        </div>
      </Section>

      {/* Confirm Dialogs */}
      <ConfirmDialog
        open={deleteConfirm !== null}
        title="Delete Credential"
        description={`Delete vault key "${deleteConfirm}"? This cannot be undone. Any connectors using this credential will stop working.`}
        variant="destructive"
        confirmLabel="Delete"
        onConfirm={handleDeleteKey}
        onCancel={() => setDeleteConfirm(null)}
      />
      <ConfirmDialog
        open={revokeConfirm !== null}
        title="Revoke Token"
        description={`Revoke execution token "${revokeConfirm}"? The associated operation will be terminated.`}
        variant="destructive"
        confirmLabel="Revoke"
        onConfirm={handleRevokeToken}
        onCancel={() => setRevokeConfirm(null)}
      />
      <ConfirmDialog
        open={clearReceiptsConfirm}
        title="Clear All Receipts"
        description="This will permanently delete all execution receipts. This action cannot be undone."
        variant="destructive"
        confirmLabel="Clear All"
        onConfirm={handleClearReceipts}
        onCancel={() => setClearReceiptsConfirm(false)}
      />
    </>
  )
}

// ================================================================
// TAB 3: Logs & Config
// ================================================================

function LogsConfigTab() {
  const [logs, setLogs] = useState<string[]>([])
  const [logFile, setLogFile] = useState('audit')
  const [logLoading, setLogLoading] = useState(false)
  const [totalLines, setTotalLines] = useState(0)
  const [configResult, setConfigResult] = useState<Record<string, string> | null>(null)
  const [exportLoading, setExportLoading] = useState(false)
  const logViewerRef = useRef<HTMLDivElement>(null)

  const loadLogs = useCallback(async () => {
    setLogLoading(true)
    try {
      const res = await fetchLogs(200, logFile)
      setLogs(res.lines)
      setTotalLines(res.total_lines)
      // Auto-scroll to bottom
      requestAnimationFrame(() => {
        if (logViewerRef.current) {
          logViewerRef.current.scrollTop = logViewerRef.current.scrollHeight
        }
      })
    } finally {
      setLogLoading(false)
    }
  }, [logFile])

  useEffect(() => {
    loadLogs()
  }, [loadLogs])

  const handleReloadConfig = async () => {
    try {
      const res = await reloadConfig()
      setConfigResult(res.results)
    } catch (e) {
      setConfigResult({ error: String(e) })
    }
  }

  const handleExport = async () => {
    setExportLoading(true)
    try {
      await exportBackup()
    } finally {
      setExportLoading(false)
    }
  }

  return (
    <>
      {/* Audit Log Viewer */}
      <Section title="Log Viewer">
        <div className="flex items-center gap-3 mb-3">
          <select
            value={logFile}
            onChange={(e) => setLogFile(e.target.value)}
            className="text-sm bg-surface-input border border-border-default rounded-md px-2 py-1 text-text-primary"
          >
            <option value="audit">Audit Log</option>
            <option value="vault">Vault Access Log</option>
          </select>
          <ActionButton label="Refresh" onClick={loadLogs} loading={logLoading} />
          <span className="text-xs text-text-muted ml-auto">{totalLines} total lines</span>
        </div>
        <div
          ref={logViewerRef}
          className="bg-[#0d1117] border border-border-default rounded-lg p-3 h-80 overflow-y-auto font-mono text-xs text-green-400 scroll-smooth"
        >
          {logs.length === 0 ? (
            <span className="text-text-muted">No log entries found.</span>
          ) : (
            logs.map((line, i) => (
              <div key={i} className="hover:bg-white/5 px-1 leading-5">
                {line}
              </div>
            ))
          )}
        </div>
      </Section>

      {/* Configuration Reload */}
      <Section title="Configuration Reload">
        <div className="flex items-center justify-between">
          <p className="text-sm text-text-secondary">
            Re-read YAML configs (feature flags, scheduler, connectors).
          </p>
          <ActionButton label="Reload Config" onClick={handleReloadConfig} />
        </div>
        {configResult && (
          <div className="mt-3 space-y-1">
            {Object.entries(configResult).map(([key, val]) => (
              <div
                key={key}
                className="flex items-center justify-between p-2 bg-surface-card-elevated rounded-md"
              >
                <span className="text-sm text-text-primary capitalize">{key}</span>
                <span
                  className={`text-xs font-mono ${
                    val.startsWith('failed') ? 'text-state-error' : 'text-state-healthy'
                  }`}
                >
                  {val}
                </span>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Export / Backup */}
      <Section title="Export / Backup">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-text-secondary">
              Download a ZIP containing configs, soul YAML, memory, flags, and scheduler data.
            </p>
          </div>
          <ActionButton label="Download Backup" onClick={handleExport} loading={exportLoading} />
        </div>
      </Section>
    </>
  )
}

// ================================================================
// TAB 4: Danger Zone
// ================================================================

function DangerTab() {
  const { data: vaultStatus } = usePolling<VaultStatusResponse>({
    fetcher: fetchVaultStatus,
    interval: 15000,
  })
  const [connectorVaultResetConfirm, setConnectorVaultResetConfirm] = useState(false)
  const [connectorVaultResetText, setConnectorVaultResetText] = useState('')
  const [factoryResetConfirm, setFactoryResetConfirm] = useState(false)
  const [factoryResetText, setFactoryResetText] = useState('')
  const [purgeConfirm, setPurgeConfirm] = useState(false)
  const [resetFlagsConfirm, setResetFlagsConfirm] = useState(false)
  const [resetOnboardingConfirm, setResetOnboardingConfirm] = useState(false)
  const [actionResult, setActionResult] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

  const handleFactoryReset = async () => {
    if (factoryResetText !== 'RESET') return
    setActionLoading(true)
    try {
      const res = await factoryReset('RESET')
      setActionResult(res.message || 'Factory reset complete')
    } catch (e) {
      setActionResult(`Error: ${e}`)
    } finally {
      setActionLoading(false)
      setFactoryResetConfirm(false)
      setFactoryResetText('')
    }
  }

  const handleConnectorVaultReset = async () => {
    if (connectorVaultResetText !== 'RESET CONNECTOR VAULT') return
    setActionLoading(true)
    try {
      const res = await resetConnectorVault('RESET CONNECTOR VAULT')
      setActionResult(res.message || 'Connector vault reset initiated')
    } catch (e) {
      const message = getErrorMessage(
        e,
        'Connector vault reset may already be in progress. The container could be restarting.',
      )
      if (message.toLowerCase().includes('failed to fetch')) {
        setActionResult('Connector vault reset initiated. The container is restarting.')
      } else {
        setActionResult(message)
      }
    } finally {
      setActionLoading(false)
      setConnectorVaultResetConfirm(false)
      setConnectorVaultResetText('')
    }
  }

  const handlePurgeMemory = async () => {
    setActionLoading(true)
    try {
      const res = await purgeMemory()
      setActionResult(`Memory purged: ${res.purged_files.join(', ') || 'no files found'}`)
    } catch (e) {
      setActionResult(`Error: ${e}`)
    } finally {
      setActionLoading(false)
      setPurgeConfirm(false)
    }
  }

  const handleResetFlags = async () => {
    setActionLoading(true)
    try {
      const res = await resetFlags()
      setActionResult(res.message || 'Flags reset')
    } catch (e) {
      setActionResult(`Error: ${e}`)
    } finally {
      setActionLoading(false)
      setResetFlagsConfirm(false)
    }
  }

  const handleResetOnboarding = async () => {
    setActionLoading(true)
    try {
      const res = await onboardingReset()
      setActionResult(res.response || 'Onboarding reset')
    } catch (e) {
      setActionResult(`Error: ${e}`)
    } finally {
      setActionLoading(false)
      setResetOnboardingConfirm(false)
    }
  }

  return (
    <>
      {/* Warning Banner */}
      <div className="bg-state-error/10 border-2 border-state-error/30 rounded-lg p-4 mb-6">
        <h3 className="text-sm font-semibold text-state-error mb-1">
          Danger Zone
        </h3>
        <p className="text-xs text-text-secondary">
          These actions are destructive and cannot be undone. Proceed with caution.
        </p>
      </div>

      {/* Reset Connector Vault */}
      <section className="bg-surface-card border-2 border-state-error/20 rounded-lg p-4 mb-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h4 className="text-sm font-medium text-text-primary">Reset Connector Vault</h4>
            <p className="text-xs text-text-secondary mt-1">
              Archive the encrypted connector vault, clear stale in-memory credentials, and restart
              the container. Use this when the vault failed closed and the stored ciphertext can no
              longer be decrypted.
            </p>
            {vaultStatus && (
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <StatusDot
                  state={getVaultStatusState(vaultStatus.status)}
                  label={vaultStatus.status.replace(/_/g, ' ')}
                />
                <span className="text-[11px] text-text-muted font-mono">
                  {vaultStatus.message}
                </span>
              </div>
            )}
          </div>
          <ActionButton
            label="Reset Connector Vault"
            variant="destructive"
            onClick={() => setConnectorVaultResetConfirm(true)}
          />
        </div>
      </section>

      {/* Factory Reset */}
      <section className="bg-surface-card border-2 border-state-error/20 rounded-lg p-4 mb-4">
        <div className="flex items-start justify-between">
          <div>
            <h4 className="text-sm font-medium text-text-primary">Factory Reset</h4>
            <p className="text-xs text-text-secondary mt-1">
              Delete all data (configs preserved in Git), reset flags, clear onboarding. The nuclear option.
            </p>
          </div>
          <ActionButton
            label="Factory Reset"
            variant="destructive"
            onClick={() => setFactoryResetConfirm(true)}
          />
        </div>
      </section>

      {/* Purge Memory */}
      <section className="bg-surface-card border-2 border-state-error/20 rounded-lg p-4 mb-4">
        <div className="flex items-start justify-between">
          <div>
            <h4 className="text-sm font-medium text-text-primary">Purge Memory</h4>
            <p className="text-xs text-text-secondary mt-1">
              Clear all memory blocks (core_blocks.json) and SQLite memory stores. Lancelot loses all learned context.
            </p>
          </div>
          <ActionButton
            label="Purge Memory"
            variant="destructive"
            onClick={() => setPurgeConfirm(true)}
          />
        </div>
      </section>

      {/* Reset Feature Flags */}
      <section className="bg-surface-card border-2 border-state-error/20 rounded-lg p-4 mb-4">
        <div className="flex items-start justify-between">
          <div>
            <h4 className="text-sm font-medium text-text-primary">Reset Feature Flags</h4>
            <p className="text-xs text-text-secondary mt-1">
              Delete .flag_state.json and reset all flags to their code defaults.
            </p>
          </div>
          <ActionButton
            label="Reset Flags"
            variant="destructive"
            onClick={() => setResetFlagsConfirm(true)}
          />
        </div>
      </section>

      {/* Reset Onboarding */}
      <section className="bg-surface-card border-2 border-state-error/20 rounded-lg p-4 mb-4">
        <div className="flex items-start justify-between">
          <div>
            <h4 className="text-sm font-medium text-text-primary">Reset Onboarding</h4>
            <p className="text-xs text-text-secondary mt-1">
              Clear all onboarding progress and restart the setup flow from scratch.
            </p>
          </div>
          <ActionButton
            label="Reset Onboarding"
            variant="destructive"
            onClick={() => setResetOnboardingConfirm(true)}
          />
        </div>
      </section>

      {/* Action Result */}
      {actionResult && (
        <Section title="Result">
          <pre className="text-sm font-mono text-text-primary bg-surface-input rounded p-3 whitespace-pre-wrap">
            {actionResult}
          </pre>
        </Section>
      )}

      {/* Reset Connector Vault â€” Custom Confirm Dialog with typed input */}
      {connectorVaultResetConfirm && (
        <dialog
          open
          className="fixed inset-0 z-50 flex items-center justify-center bg-transparent"
          style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}
        >
          <div className="bg-surface-card-elevated border border-state-error/30 rounded-lg p-6 max-w-md w-full shadow-xl">
            <h3 className="text-lg font-semibold text-state-error">Reset Connector Vault</h3>
            <p className="text-sm text-text-secondary mt-2">
              This archives the current encrypted vault artifacts under the reset backup directory
              and immediately restarts the container so stale in-memory credentials are gone. Type{' '}
              <span className="font-mono font-bold text-state-error">RESET CONNECTOR VAULT</span>{' '}
              to confirm.
            </p>
            <input
              type="text"
              value={connectorVaultResetText}
              onChange={(e) => setConnectorVaultResetText(e.target.value)}
              placeholder="Type RESET CONNECTOR VAULT"
              className="w-full mt-4 px-3 py-2 text-sm bg-surface-input border border-border-default rounded-md text-text-primary font-mono focus:outline-none focus:border-state-error"
            />
            <div className="flex justify-end gap-3 mt-4">
              <button
                onClick={() => {
                  setConnectorVaultResetConfirm(false)
                  setConnectorVaultResetText('')
                }}
                className="px-4 py-2 text-sm text-text-secondary bg-surface-input border border-border-default rounded-md hover:bg-surface-card transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleConnectorVaultReset}
                disabled={connectorVaultResetText !== 'RESET CONNECTOR VAULT' || actionLoading}
                className="px-4 py-2 text-sm font-medium rounded-md bg-state-error hover:bg-state-error/80 text-white transition-colors disabled:opacity-50"
              >
                {actionLoading ? 'Resetting...' : 'Reset Connector Vault'}
              </button>
            </div>
          </div>
        </dialog>
      )}

      {/* Factory Reset â€” Custom Confirm Dialog with typed input */}
      {factoryResetConfirm && (
        <dialog
          open
          className="fixed inset-0 z-50 flex items-center justify-center bg-transparent"
          style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}
        >
          <div className="bg-surface-card-elevated border border-state-error/30 rounded-lg p-6 max-w-md w-full shadow-xl">
            <h3 className="text-lg font-semibold text-state-error">Factory Reset</h3>
            <p className="text-sm text-text-secondary mt-2">
              This will permanently delete all data in the data directory. This action cannot be
              undone. Type <span className="font-mono font-bold text-state-error">RESET</span> to
              confirm.
            </p>
            <input
              type="text"
              value={factoryResetText}
              onChange={(e) => setFactoryResetText(e.target.value)}
              placeholder="Type RESET to confirm"
              className="w-full mt-4 px-3 py-2 text-sm bg-surface-input border border-border-default rounded-md text-text-primary font-mono focus:outline-none focus:border-state-error"
            />
            <div className="flex justify-end gap-3 mt-4">
              <button
                onClick={() => {
                  setFactoryResetConfirm(false)
                  setFactoryResetText('')
                }}
                className="px-4 py-2 text-sm text-text-secondary bg-surface-input border border-border-default rounded-md hover:bg-surface-card transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleFactoryReset}
                disabled={factoryResetText !== 'RESET' || actionLoading}
                className="px-4 py-2 text-sm font-medium rounded-md bg-state-error hover:bg-state-error/80 text-white transition-colors disabled:opacity-50"
              >
                {actionLoading ? 'Resetting...' : 'Factory Reset'}
              </button>
            </div>
          </div>
        </dialog>
      )}

      {/* Other confirm dialogs */}
      <ConfirmDialog
        open={purgeConfirm}
        title="Purge All Memory"
        description="This will permanently delete all memory blocks and SQLite stores. Lancelot will lose all learned context and memory. This cannot be undone."
        variant="destructive"
        confirmLabel="Purge"
        onConfirm={handlePurgeMemory}
        onCancel={() => setPurgeConfirm(false)}
      />
      <ConfirmDialog
        open={resetFlagsConfirm}
        title="Reset Feature Flags"
        description="This will delete the persisted flag state and reset all feature flags to their code defaults. Some subsystems may turn on or off."
        variant="destructive"
        confirmLabel="Reset Flags"
        onConfirm={handleResetFlags}
        onCancel={() => setResetFlagsConfirm(false)}
      />
      <ConfirmDialog
        open={resetOnboardingConfirm}
        title="Reset Onboarding"
        description="This will clear all onboarding progress and restart the setup flow from scratch. This action cannot be undone."
        variant="destructive"
        confirmLabel="Reset"
        onConfirm={handleResetOnboarding}
        onCancel={() => setResetOnboardingConfirm(false)}
      />
    </>
  )
}
