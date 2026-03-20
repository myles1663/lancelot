import { useState } from 'react'
import { usePolling } from '@/hooks'
import {
  fetchMCPServers,
  registerMCPServer,
  unregisterMCPServer,
  setMCPServerStatus,
  testMCPServer,
  storeMCPCredential,
} from '@/api'
import type { MCPServersListResponse, MCPTestResult } from '@/api/mcp'
import { StatusDot, ConfirmDialog } from '@/components'

// ── Status helpers ──────────────────────────────────────────

function serverStatusColor(status: string): 'healthy' | 'degraded' | 'error' | 'inactive' {
  switch (status) {
    case 'active': return 'healthy'
    case 'validated': return 'degraded'
    case 'registered': return 'inactive'
    case 'suspended': return 'error'
    case 'error': return 'error'
    default: return 'inactive'
  }
}

function riskTierBadge(tier: string): string {
  switch (tier) {
    case 'T0': return 'bg-green-500/15 text-green-400'
    case 'T1': return 'bg-blue-500/15 text-blue-400'
    case 'T2': return 'bg-amber-500/15 text-amber-400'
    case 'T3': return 'bg-red-500/15 text-red-400'
    default: return 'bg-surface-input text-text-muted'
  }
}

// ── Register Modal ──────────────────────────────────────────

function RegisterForm({
  onRegister,
  onCancel,
}: {
  onRegister: (config: {
    server_id: string
    name: string
    endpoint: string
    auth_type: string
    vault_key: string
    auth_header: string
    default_risk_tier: string
    network_domains: string[]
  }) => void
  onCancel: () => void
}) {
  const [serverId, setServerId] = useState('')
  const [name, setName] = useState('')
  const [endpoint, setEndpoint] = useState('')
  const [authType, setAuthType] = useState('none')
  const [vaultKey, setVaultKey] = useState('')
  const [authHeader, setAuthHeader] = useState('')
  const [riskTier, setRiskTier] = useState('T2')
  const [domains, setDomains] = useState('')

  const canSubmit = serverId.trim() && name.trim() && endpoint.trim()

  return (
    <div className="p-4 bg-surface-card border border-accent-primary/30 rounded-lg space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-accent-primary">Register MCP Server</h4>
        <button onClick={onCancel} className="text-xs text-text-muted hover:text-text-primary">Cancel</button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-[10px] text-text-muted uppercase tracking-wider">Server ID</label>
          <input
            value={serverId}
            onChange={e => setServerId(e.target.value.replace(/[^a-z0-9_-]/g, ''))}
            placeholder="github-mcp"
            className="w-full mt-0.5 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
          />
        </div>
        <div>
          <label className="text-[10px] text-text-muted uppercase tracking-wider">Display Name</label>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="GitHub MCP Server"
            className="w-full mt-0.5 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
          />
        </div>
      </div>

      <div>
        <label className="text-[10px] text-text-muted uppercase tracking-wider">Endpoint URL</label>
        <input
          value={endpoint}
          onChange={e => setEndpoint(e.target.value)}
          placeholder="https://mcp.example.com/sse"
          className="w-full mt-0.5 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
        />
        <p className="text-[9px] text-text-muted mt-0.5">HTTP+SSE transport only. HTTPS required for remote endpoints.</p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="text-[10px] text-text-muted uppercase tracking-wider">Auth Type</label>
          <select
            value={authType}
            onChange={e => setAuthType(e.target.value)}
            className="w-full mt-0.5 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs text-text-primary focus:outline-none focus:border-accent-primary"
          >
            <option value="none">None</option>
            <option value="api_key">API Key (Bearer)</option>
            <option value="oauth2">OAuth 2.0</option>
            <option value="basic">Basic Auth</option>
            <option value="custom_header">Custom Header</option>
          </select>
        </div>
        <div>
          <label className="text-[10px] text-text-muted uppercase tracking-wider">Risk Tier</label>
          <select
            value={riskTier}
            onChange={e => setRiskTier(e.target.value)}
            className="w-full mt-0.5 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs text-text-primary focus:outline-none focus:border-accent-primary"
          >
            <option value="T0">T0 — Read-only</option>
            <option value="T1">T1 — Reversible</option>
            <option value="T2">T2 — Controlled</option>
            <option value="T3">T3 — Irreversible</option>
          </select>
        </div>
        {authType === 'custom_header' && (
          <div>
            <label className="text-[10px] text-text-muted uppercase tracking-wider">Header Name</label>
            <input
              value={authHeader}
              onChange={e => setAuthHeader(e.target.value)}
              placeholder="X-API-Key"
              className="w-full mt-0.5 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
            />
          </div>
        )}
      </div>

      {authType !== 'none' && (
        <div>
          <label className="text-[10px] text-text-muted uppercase tracking-wider">Vault Key</label>
          <input
            value={vaultKey}
            onChange={e => setVaultKey(e.target.value)}
            placeholder="mcp.github_token"
            className="w-full mt-0.5 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
          />
          <p className="text-[9px] text-text-muted mt-0.5">Credential Vault key reference. Credential is stored encrypted, never in config.</p>
        </div>
      )}

      <div>
        <label className="text-[10px] text-text-muted uppercase tracking-wider">Network Domains</label>
        <input
          value={domains}
          onChange={e => setDomains(e.target.value)}
          placeholder="mcp.example.com, api.example.com"
          className="w-full mt-0.5 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
        />
        <p className="text-[9px] text-text-muted mt-0.5">Comma-separated. Must be in the network allowlist.</p>
      </div>

      <button
        onClick={() => onRegister({
          server_id: serverId.trim(),
          name: name.trim(),
          endpoint: endpoint.trim(),
          auth_type: authType,
          vault_key: vaultKey.trim(),
          auth_header: authHeader.trim(),
          default_risk_tier: riskTier,
          network_domains: domains.split(',').map(d => d.trim()).filter(Boolean),
        })}
        disabled={!canSubmit}
        className={`px-4 py-1.5 text-[11px] font-medium rounded transition-colors ${
          canSubmit
            ? 'bg-accent-primary text-white hover:bg-accent-primary/80'
            : 'bg-surface-input text-text-muted cursor-not-allowed'
        }`}
      >
        Register Server
      </button>
    </div>
  )
}

// ── Main MCP Section ────────────────────────────────────────

export function MCPSection() {
  const { data, refetch } = usePolling<MCPServersListResponse>({
    fetcher: fetchMCPServers,
    interval: 10000,
  })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [showRegister, setShowRegister] = useState(false)
  const [, setRegistering] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, MCPTestResult>>({})
  const [removeConfirm, setRemoveConfirm] = useState<string | null>(null)
  const [suspendConfirm, setSuspendConfirm] = useState<string | null>(null)
  const [credInputs, setCredInputs] = useState<Record<string, string>>({})
  const [savingCred, setSavingCred] = useState<string | null>(null)

  const servers = data?.servers ?? []
  const featureEnabled = data?.feature_enabled ?? false

  const toggleExpand = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleRegister = async (config: Parameters<typeof registerMCPServer>[0]) => {
    setRegistering(true)
    try {
      await registerMCPServer(config)
      setShowRegister(false)
      refetch()
    } catch { /* ignore */ } finally {
      setRegistering(false)
    }
  }

  const handleRemove = async () => {
    if (!removeConfirm) return
    try {
      await unregisterMCPServer(removeConfirm)
      refetch()
    } catch { /* ignore */ } finally {
      setRemoveConfirm(null)
    }
  }

  const handleSuspend = async () => {
    if (!suspendConfirm) return
    try {
      await setMCPServerStatus(suspendConfirm, 'suspended')
      refetch()
    } catch { /* ignore */ } finally {
      setSuspendConfirm(null)
    }
  }

  const handleActivate = async (serverId: string) => {
    try {
      await setMCPServerStatus(serverId, 'active')
      refetch()
    } catch { /* ignore */ }
  }

  const handleTest = async (serverId: string) => {
    setTesting(serverId)
    try {
      const result = await testMCPServer(serverId)
      setTestResults(prev => ({ ...prev, [serverId]: result }))
    } catch {
      setTestResults(prev => ({ ...prev, [serverId]: { success: false, tool_count: 0, latency_ms: 0, error: 'Request failed' } }))
    } finally {
      setTesting(null)
    }
  }

  const handleSaveCred = async (serverId: string, vaultKey: string, authType: string) => {
    const value = credInputs[serverId]
    if (!value) return
    setSavingCred(serverId)
    try {
      await storeMCPCredential(serverId, vaultKey, value, authType === 'api_key' ? 'api_key' : authType)
      setCredInputs(prev => ({ ...prev, [serverId]: '' }))
      refetch()
    } catch { /* ignore */ } finally {
      setSavingCred(null)
    }
  }

  return (
    <div className="mt-8">
      {/* Section Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h3 className="text-base font-semibold text-text-primary">MCP Servers</h3>
          <span className={`text-[9px] px-1.5 py-0.5 rounded font-medium ${
            featureEnabled ? 'bg-state-healthy/15 text-state-healthy' : 'bg-state-error/15 text-state-error'
          }`}>
            {featureEnabled ? 'ENABLED' : 'DISABLED'}
          </span>
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400">HTTP+SSE</span>
        </div>
        {featureEnabled && (
          <button
            onClick={() => setShowRegister(!showRegister)}
            className="px-3 py-1.5 text-[11px] font-medium rounded bg-accent-primary/15 text-accent-primary hover:bg-accent-primary/25 transition-colors"
          >
            {showRegister ? 'Cancel' : '+ Register Server'}
          </button>
        )}
      </div>

      {!featureEnabled && (
        <div className="bg-surface-card border border-border-default rounded-lg p-4 text-center">
          <p className="text-sm text-text-muted">
            MCP is disabled. Enable <span className="font-mono text-text-secondary">FEATURE_MCP</span> in Kill Switches to use governed MCP servers.
          </p>
          <p className="text-[10px] text-text-muted mt-1">
            All MCP invocations route through: Soul permission → Kill switch → Network allowlist → Argument screening → Execution → Receipt
          </p>
        </div>
      )}

      {/* Summary stats */}
      {featureEnabled && data && (
        <div className="bg-surface-card border border-border-default rounded-lg p-3 mb-4">
          <div className="grid grid-cols-3 gap-4">
            <div>
              <span className="text-[10px] uppercase tracking-wider text-text-muted">Total Servers</span>
              <p className="text-lg font-mono text-text-primary mt-0.5">{data.total}</p>
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-text-muted">Active</span>
              <p className="text-lg font-mono text-state-healthy mt-0.5">{data.active_count}</p>
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-text-muted">Transport</span>
              <p className="text-sm font-mono text-purple-400 mt-1">HTTP+SSE only</p>
            </div>
          </div>
        </div>
      )}

      {/* Register form */}
      {showRegister && (
        <div className="mb-4">
          <RegisterForm
            onRegister={handleRegister}
            onCancel={() => setShowRegister(false)}
          />
        </div>
      )}

      {/* Server cards */}
      {featureEnabled && servers.length > 0 && (
        <div className="space-y-2">
          {servers.map(server => {
            const isExpanded = expanded.has(server.server_id)
            const test = testResults[server.server_id]

            return (
              <div key={server.server_id} className="bg-surface-card-elevated rounded-md border border-border-default overflow-hidden">
                {/* Header */}
                <div
                  className="flex items-center justify-between p-3 cursor-pointer hover:bg-surface-input/50 transition-colors"
                  onClick={() => toggleExpand(server.server_id)}
                >
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <span className={`text-[10px] transition-transform ${isExpanded ? 'rotate-90' : ''}`}>&#9654;</span>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-text-primary">{server.name}</span>
                        <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-input text-text-muted">
                          {server.server_id}
                        </span>
                        <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${riskTierBadge(server.default_risk_tier)}`}>
                          {server.default_risk_tier}
                        </span>
                        {server.tool_count > 0 && (
                          <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-input text-text-muted">
                            {server.tool_count} tools
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-text-muted truncate mt-0.5 font-mono">{server.endpoint}</p>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 flex-shrink-0">
                    <StatusDot
                      state={serverStatusColor(server.status)}
                      label={server.status}
                    />
                    {server.status === 'suspended' ? (
                      <button
                        onClick={e => { e.stopPropagation(); handleActivate(server.server_id) }}
                        className="px-2 py-1 text-[10px] font-medium rounded bg-state-healthy/15 text-state-healthy hover:bg-state-healthy/25"
                      >
                        Activate
                      </button>
                    ) : server.status !== 'error' ? (
                      <button
                        onClick={e => { e.stopPropagation(); setSuspendConfirm(server.server_id) }}
                        className="px-2 py-1 text-[10px] font-medium rounded bg-state-error/10 text-state-error hover:bg-state-error/20"
                      >
                        Suspend
                      </button>
                    ) : null}
                  </div>
                </div>

                {/* Expanded */}
                {isExpanded && (
                  <div className="px-3 pb-3 pt-0 border-t border-border-default/50 space-y-3">
                    {/* Details grid */}
                    <div className="grid grid-cols-4 gap-3 mt-2 text-[11px]">
                      <div>
                        <span className="text-text-muted uppercase text-[10px] tracking-wider">Auth</span>
                        <p className="text-text-secondary mt-0.5">{server.auth_type}</p>
                      </div>
                      <div>
                        <span className="text-text-muted uppercase text-[10px] tracking-wider">Credentials</span>
                        <p className={`mt-0.5 ${server.has_credentials ? 'text-state-healthy' : 'text-text-muted'}`}>
                          {server.has_credentials ? 'In Vault' : 'None'}
                        </p>
                      </div>
                      <div>
                        <span className="text-text-muted uppercase text-[10px] tracking-wider">Kill Switch</span>
                        <p className="text-text-secondary mt-0.5 font-mono">{server.kill_switch_id || 'auto'}</p>
                      </div>
                      <div>
                        <span className="text-text-muted uppercase text-[10px] tracking-wider">Registered</span>
                        <p className="text-text-secondary mt-0.5">
                          {server.registered_at ? new Date(server.registered_at).toLocaleDateString() : '—'}
                        </p>
                      </div>
                    </div>

                    {/* Network domains */}
                    {server.network_domains.length > 0 && (
                      <div>
                        <span className="text-[10px] text-text-muted uppercase tracking-wider">Network Domains</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {server.network_domains.map(d => (
                            <span key={d} className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-input text-text-muted">
                              {d}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Credential input (if server uses auth) */}
                    {server.auth_type !== 'none' && (
                      <div className="p-3 bg-surface-card rounded-lg border border-border-default">
                        <span className="text-[10px] text-text-muted uppercase tracking-wider">Server Credential</span>
                        <div className="flex gap-2 mt-1">
                          <input
                            type="password"
                            placeholder={server.has_credentials ? '••••••••' : `Enter ${server.auth_type}...`}
                            value={credInputs[server.server_id] || ''}
                            onChange={e => setCredInputs(prev => ({ ...prev, [server.server_id]: e.target.value }))}
                            className="flex-1 bg-surface-input border border-border-default rounded px-2 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent-primary"
                          />
                          <button
                            onClick={() => handleSaveCred(server.server_id, `mcp.${server.server_id}`, server.auth_type)}
                            disabled={savingCred === server.server_id || !credInputs[server.server_id]}
                            className={`px-3 py-1 text-[11px] font-medium rounded transition-colors ${
                              credInputs[server.server_id]
                                ? 'bg-accent-primary text-white hover:bg-accent-primary/80'
                                : 'bg-surface-input text-text-muted cursor-not-allowed'
                            }`}
                          >
                            {savingCred === server.server_id ? 'Saving...' : 'Save'}
                          </button>
                        </div>
                      </div>
                    )}

                    {/* Actions */}
                    <div className="flex items-center gap-2 pt-2 border-t border-border-default/50">
                      <button
                        onClick={() => handleTest(server.server_id)}
                        disabled={testing === server.server_id}
                        className="px-3 py-1.5 text-[11px] font-medium rounded bg-accent-primary text-white hover:bg-accent-primary/80 transition-colors disabled:opacity-50"
                      >
                        {testing === server.server_id ? 'Testing...' : 'Test Connection'}
                      </button>
                      {test && (
                        <span className={`text-[11px] ${test.success ? 'text-state-healthy' : 'text-state-error'}`}>
                          {test.success
                            ? `OK — ${test.tool_count} tools discovered (${test.latency_ms}ms)`
                            : test.error || 'Connection failed'}
                        </span>
                      )}
                      <div className="flex-1" />
                      <button
                        onClick={() => setRemoveConfirm(server.server_id)}
                        className="px-3 py-1.5 text-[11px] font-medium rounded bg-state-error/10 text-state-error hover:bg-state-error/20 transition-colors"
                      >
                        Unregister
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {featureEnabled && servers.length === 0 && !showRegister && (
        <div className="bg-surface-card border border-border-default rounded-lg p-6 text-center">
          <p className="text-sm text-text-muted">No MCP servers registered.</p>
          <p className="text-[10px] text-text-muted mt-1">
            Click "Register Server" to connect an MCP server through the governed proxy.
          </p>
        </div>
      )}

      {/* Governance info */}
      {featureEnabled && (
        <div className="mt-3 p-3 bg-surface-card border border-border-default/50 rounded-lg">
          <p className="text-[10px] text-text-muted leading-relaxed">
            <span className="font-semibold text-text-secondary">Governance Pipeline:</span>{' '}
            Soul Permission → Kill Switch → Server Status → Network Allowlist → Argument Screening
            (SQL/NoSQL/path traversal/command injection/prompt injection/SSRF) → Credential Resolution
            (Vault-scoped) → MCP Execution → Response Guard (credential scrub) → Receipt (mandatory).
            Receipt write failure = result discarded.
          </p>
        </div>
      )}

      {/* Confirm dialogs */}
      <ConfirmDialog
        open={removeConfirm !== null}
        title="Unregister MCP Server"
        description={`This will remove the ${removeConfirm ?? ''} MCP server from the registry. Vault credentials will be preserved. Continue?`}
        variant="destructive"
        confirmLabel="Unregister"
        onConfirm={handleRemove}
        onCancel={() => setRemoveConfirm(null)}
      />

      <ConfirmDialog
        open={suspendConfirm !== null}
        title="Suspend MCP Server"
        description={`This will suspend the ${suspendConfirm ?? ''} MCP server. All tool invocations will be blocked until reactivated.`}
        variant="destructive"
        confirmLabel="Suspend"
        onConfirm={handleSuspend}
        onCancel={() => setSuspendConfirm(null)}
      />
    </div>
  )
}
