import { useState, useRef, useEffect, useCallback } from 'react'
import { usePolling } from '@/hooks'
import {
  fetchOnboardingStatus,
  sendOnboardingCommand,
  onboardingBack,
  onboardingRestartStep,
  onboardingResendCode,
  onboardingReset,
  fetchSystemInfo,
  restartContainer,
  shutdownContainer,
  fetchLogs,
  fetchVaultKeys,
  deleteVaultKey,
  fetchTokens,
  revokeToken,
  clearReceipts,
  resetUsage,
  reloadConfig,
  exportBackup,
  factoryReset,
  purgeMemory,
  resetFlags,
} from '@/api'
import { fetchReceiptStats } from '@/api/receipts'
import { MetricCard, StatusDot, ConfirmDialog, EmptyState } from '@/components'
import type {
  SystemInfoResponse,
  VaultKeyEntry,
  ExecutionToken,
} from '@/types/api'

// ── Helpers ─────────────────────────────────────────────────────

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function formatTimestamp(iso: string): string {
  if (!iso) return 'N/A'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

// ── Tab definitions ─────────────────────────────────────────────

const TABS = [
  { id: 'system', label: 'System' },
  { id: 'data', label: 'Data' },
  { id: 'logs', label: 'Logs & Config' },
  { id: 'danger', label: 'Danger Zone' },
] as const

type TabId = (typeof TABS)[number]['id']

// ── Section wrapper ─────────────────────────────────────────────

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

// ── Button helpers ──────────────────────────────────────────────

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

// ── Main Component ──────────────────────────────────────────────

export function SetupRecovery() {
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
      // Expected — connection will drop
    } finally {
      setConfirmAction(null)
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
                Local Model
              </span>
              <p className="text-sm font-mono text-text-primary mt-1">
                {onboarding.local_model_status}
              </p>
            </div>
          </div>
        )}
        {onboarding?.cooldown_active && (
          <div className="mt-4 p-3 bg-state-degraded/10 border border-state-degraded/30 rounded">
            <span className="text-xs font-semibold text-state-degraded">
              Cooldown Active — {Math.round(onboarding.cooldown_remaining)}s remaining
            </span>
            {onboarding.last_error && (
              <p className="text-xs text-text-secondary mt-1">{onboarding.last_error}</p>
            )}
          </div>
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
  const { data: vaultData, refetch: refetchVault } = usePolling({
    fetcher: fetchVaultKeys,
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
      {/* Vault Credentials */}
      <Section title="Vault Credentials">
        {!vaultData ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : vaultData.keys.length === 0 ? (
          <EmptyState title="No Credentials" description="No credentials stored in the vault." />
        ) : (
          <div className="space-y-2">
            {vaultData.keys.map((entry: VaultKeyEntry) => (
              <div
                key={entry.key}
                className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md"
              >
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-mono text-text-primary">{entry.key}</span>
                  <div className="flex gap-3 mt-0.5">
                    <span className="text-[10px] text-text-muted">Type: {entry.type}</span>
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
          Credential values are never shown. Only keys and metadata are displayed.
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

      {/* Factory Reset — Custom Confirm Dialog with typed input */}
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
