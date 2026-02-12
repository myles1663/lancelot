import { useState } from 'react'
import { usePolling } from '@/hooks'
import {
  fetchConnectors,
  enableConnector,
  disableConnector,
  setConnectorBackend,
  storeCredential,
  deleteCredential,
  validateCredentials,
} from '@/api'
import type { ConnectorInfo, CredentialInfo } from '@/api/connectors'
import { StatusDot, ConfirmDialog } from '@/components'

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

// ── Main Page ────────────────────────────────────────────────────
export function Connectors() {
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

  const connectors = data?.connectors ?? []

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
    } catch { /* ignore */ } finally {
      setToggling(null)
    }
  }

  const doDisable = async (id: string) => {
    setDisableConfirm(null)
    setToggling(id)
    try {
      await disableConnector(id)
      refetch()
    } catch { /* ignore */ } finally {
      setToggling(null)
    }
  }

  const handleBackendChange = async (id: string, backend: string) => {
    try {
      await setConnectorBackend(id, backend)
      refetch()
    } catch { /* ignore */ }
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
    } catch { /* ignore */ } finally {
      setSaving(null)
    }
  }

  const handleDeleteCred = async () => {
    if (!deleteConfirm) return
    try {
      await deleteCredential(deleteConfirm.connectorId, deleteConfirm.vaultKey)
      refetch()
    } catch { /* ignore */ } finally {
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
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Connectors</h2>

      {/* Summary Bar */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <div className="grid grid-cols-3 gap-4">
          <div>
            <span className="text-[10px] uppercase tracking-wider text-text-muted">Total</span>
            <p className="text-xl font-mono text-text-primary mt-1">{data?.total ?? 0}</p>
          </div>
          <div>
            <span className="text-[10px] uppercase tracking-wider text-text-muted">Enabled</span>
            <p className="text-xl font-mono text-state-healthy mt-1">{data?.enabled_count ?? 0}</p>
          </div>
          <div>
            <span className="text-[10px] uppercase tracking-wider text-text-muted">Configured</span>
            <p className="text-xl font-mono text-accent-primary mt-1">{data?.configured_count ?? 0}</p>
          </div>
        </div>
      </section>

      {/* Connector Cards */}
      {!data ? (
        <p className="text-sm text-text-muted">Loading connectors...</p>
      ) : connectors.length === 0 ? (
        <section className="bg-surface-card border border-border-default rounded-lg p-6 text-center">
          <p className="text-sm text-text-muted">No connectors available. Ensure FEATURE_CONNECTORS is enabled.</p>
        </section>
      ) : (
        <div className="space-y-2">
          {connectors.map(connector => {
            const isExpanded = expanded.has(connector.id)
            const isConfiguring = configuring.has(connector.id)
            const cState = credentialState(connector.credentials)

            return (
              <div key={connector.id} className="bg-surface-card-elevated rounded-md border border-border-default overflow-hidden">
                {/* Header row */}
                <div
                  className="flex items-center justify-between p-3 cursor-pointer hover:bg-surface-input/50 transition-colors"
                  onClick={() => toggleExpand(connector.id)}
                >
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <span className={`text-[10px] transition-transform ${isExpanded ? 'rotate-90' : ''}`}>&#9654;</span>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-text-primary">{connector.name}</span>
                        <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-input text-text-muted">
                          {connector.operation_count} ops
                        </span>
                        {connector.backend && (
                          <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-accent-primary/15 text-accent-primary">
                            {connector.backend}
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-text-muted truncate mt-0.5">{connector.description}</p>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 flex-shrink-0">
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
                  <div className="px-3 pb-3 pt-0 border-t border-border-default/50 space-y-3">
                    {/* Backend selector */}
                    {connector.available_backends && connector.available_backends.length > 1 && (
                      <div className="flex items-center gap-2 mt-2">
                        <span className="text-[10px] uppercase tracking-wider text-text-muted">Backend:</span>
                        <select
                          value={connector.backend || ''}
                          onChange={(e) => { e.stopPropagation(); handleBackendChange(connector.id, e.target.value) }}
                          className="text-xs bg-surface-input border border-border-default rounded px-2 py-1 text-text-primary focus:outline-none focus:border-accent-primary"
                        >
                          {connector.available_backends.map(b => (
                            <option key={b} value={b}>{b}</option>
                          ))}
                        </select>
                      </div>
                    )}

                    {/* Data access summary */}
                    <div className="grid grid-cols-3 gap-3 mt-2">
                      <div>
                        <span className="text-[10px] text-text-muted uppercase tracking-wider">Reads</span>
                        <ul className="mt-1 space-y-0.5">
                          {connector.data_reads.map((r, i) => (
                            <li key={i} className="text-[11px] text-text-secondary">{r}</li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <span className="text-[10px] text-text-muted uppercase tracking-wider">Writes</span>
                        <ul className="mt-1 space-y-0.5">
                          {connector.data_writes.map((w, i) => (
                            <li key={i} className="text-[11px] text-text-secondary">{w}</li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <span className="text-[10px] text-state-healthy uppercase tracking-wider">Does Not Access</span>
                        <ul className="mt-1 space-y-0.5">
                          {connector.does_not_access.map((d, i) => (
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
                      className="px-3 py-1.5 text-[11px] font-medium rounded bg-surface-input border border-border-default text-text-secondary hover:text-text-primary hover:bg-surface-card transition-colors"
                    >
                      {isConfiguring ? 'Hide Credentials' : 'Configure Credentials'}
                    </button>

                    {/* Credential form */}
                    {isConfiguring && (
                      <div className="p-3 bg-surface-card rounded-lg border border-border-default">
                        <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wider">
                          Credentials ({connector.credentials.filter(c => c.present).length}/{connector.credentials.length} configured)
                        </span>

                        {connector.credentials.map(cred => (
                          <div key={cred.vault_key} className="mt-3">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-[11px] font-medium text-text-primary">{cred.name}</span>
                              <span className={`text-[9px] px-1.5 py-0.5 rounded ${
                                cred.present ? 'bg-state-healthy/15 text-state-healthy' : 'bg-state-error/15 text-state-error'
                              }`}>
                                {cred.present ? 'stored' : 'missing'}
                              </span>
                              <span className="text-[9px] px-1.5 py-0.5 rounded bg-surface-input text-text-muted">{cred.type}</span>
                              {cred.required && <span className="text-[9px] text-state-degraded">required</span>}
                            </div>
                            <div className="flex gap-2">
                              <input
                                type="password"
                                placeholder={cred.present ? '••••••••' : `Enter ${cred.type}...`}
                                value={credInputs[connector.id]?.[cred.vault_key] || ''}
                                onChange={(e) => updateCredInput(connector.id, cred.vault_key, e.target.value)}
                                className="flex-1 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
                              />
                              <button
                                onClick={() => handleSaveCred(connector.id, cred)}
                                disabled={saving === `${connector.id}.${cred.vault_key}` || !credInputs[connector.id]?.[cred.vault_key]}
                                className={`px-3 py-1 text-[11px] font-medium rounded transition-colors ${
                                  credInputs[connector.id]?.[cred.vault_key]
                                    ? 'bg-accent-primary text-white hover:bg-accent-primary/80'
                                    : 'bg-surface-input text-text-muted cursor-not-allowed'
                                }`}
                              >
                                {saving === `${connector.id}.${cred.vault_key}` ? 'Saving...' : 'Save'}
                              </button>
                              {cred.present && (
                                <button
                                  onClick={() => setDeleteConfirm({ connectorId: connector.id, vaultKey: cred.vault_key, name: cred.name })}
                                  className="px-3 py-1 text-[11px] font-medium rounded bg-state-error/10 text-state-error hover:bg-state-error/20 transition-colors"
                                >
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
                        ))}

                        {/* Test Connection */}
                        <div className="flex items-center gap-2 mt-4 pt-3 border-t border-border-default/50">
                          <button
                            onClick={() => handleValidate(connector.id)}
                            disabled={validating === connector.id}
                            className="px-3 py-1.5 text-[11px] font-medium rounded bg-accent-primary text-white hover:bg-accent-primary/80 transition-colors disabled:opacity-50"
                          >
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
    </div>
  )
}
