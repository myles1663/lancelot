import { useState, useCallback } from 'react'
import { usePolling, usePageTitle } from '@/hooks'
import {
  fetchExportFormats,
  fetchExportHistory,
  generateExport,
  verifyExport,
  getExportDownloadUrl,
} from '@/api/compliance'
import type {
  ExportFormat,
  ExportResponse,
  ExportHistoryEntry,
  VerifyResult,
} from '@/api/compliance'
import { MetricCard, StatusDot, EmptyState } from '@/components'
import { formatTimestamp } from '@/utils/dateFormat'

// ── Helpers ──────────────────────────────────────────────────────

const FORMAT_LABELS: Record<string, string> = {
  PDF: 'Forensic Timeline PDF',
  SOC2_JSON: 'SOC 2 Type II',
  ISO27001_JSON: 'ISO 27001:2022',
  GDPR_JSON: 'GDPR Art. 30',
}

const FORMAT_ICONS: Record<string, string> = {
  PDF: 'PDF',
  SOC2_JSON: 'SOC2',
  ISO27001_JSON: 'ISO',
  GDPR_JSON: 'GDPR',
}

function chainBadge(status: string) {
  const intact = status === 'CHAIN_INTACT'
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono ${
        intact
          ? 'bg-status-success/10 text-status-success'
          : 'bg-status-error/10 text-status-error'
      }`}
    >
      <StatusDot state={intact ? 'healthy' : 'error'} />
      {status}
    </span>
  )
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

function daysAgoISO(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

// ── Period Presets ────────────────────────────────────────────────

const PERIOD_PRESETS = [
  { label: 'Last 7 days', start: () => daysAgoISO(7), end: todayISO },
  { label: 'Last 30 days', start: () => daysAgoISO(30), end: todayISO },
  { label: 'Last 90 days', start: () => daysAgoISO(90), end: todayISO },
  { label: 'Year to date', start: () => `${new Date().getFullYear()}-01-01`, end: todayISO },
]

// ── Component ────────────────────────────────────────────────────

export function ComplianceExport() {
  usePageTitle('Compliance Export')

  // Data polling
  const { data: formatsData } = usePolling({ fetcher: fetchExportFormats, interval: 60000 })
  const { data: historyData, refetch: refetchHistory } = usePolling({
    fetcher: fetchExportHistory,
    interval: 15000,
  })

  // Export form state
  const [selectedFormat, setSelectedFormat] = useState('PDF')
  const [periodStart, setPeriodStart] = useState(() => daysAgoISO(30))
  const [periodEnd, setPeriodEnd] = useState(todayISO)
  const [questId, setQuestId] = useState('')
  const [anomalyThreshold, setAnomalyThreshold] = useState(5)

  // Export state
  const [generating, setGenerating] = useState(false)
  const [lastResult, setLastResult] = useState<ExportResponse | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)

  // Verify state
  const [verifying, setVerifying] = useState<string | null>(null)
  const [verifyResults, setVerifyResults] = useState<Record<string, VerifyResult>>({})

  const formats: ExportFormat[] = formatsData?.formats ?? []
  const history: ExportHistoryEntry[] = historyData?.exports ?? []
  const historyTotal = historyData?.total ?? 0

  // ── Handlers ─────────────────────────────────────────────────

  const handleGenerate = useCallback(async () => {
    setGenerating(true)
    setExportError(null)
    setLastResult(null)
    try {
      const result = await generateExport({
        format: selectedFormat,
        period_start: `${periodStart}T00:00:00Z`,
        period_end: `${periodEnd}T23:59:59Z`,
        quest_id: questId || undefined,
        anomaly_threshold: anomalyThreshold,
      })
      setLastResult(result)
      if (!result.success) {
        setExportError(result.error || 'Export failed')
      }
      refetchHistory()
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export request failed')
    } finally {
      setGenerating(false)
    }
  }, [selectedFormat, periodStart, periodEnd, questId, anomalyThreshold, refetchHistory])

  const handleVerify = useCallback(async (exportId: string) => {
    setVerifying(exportId)
    try {
      const result = await verifyExport(exportId)
      setVerifyResults((prev) => ({ ...prev, [exportId]: result }))
    } catch {
      setVerifyResults((prev) => ({
        ...prev,
        [exportId]: {
          export_id: exportId,
          verified: false,
          current_sha256: '',
          reason: 'Verification request failed',
        },
      }))
    } finally {
      setVerifying(null)
    }
  }, [])

  const applyPreset = (preset: (typeof PERIOD_PRESETS)[number]) => {
    setPeriodStart(preset.start())
    setPeriodEnd(preset.end())
  }

  // ── Render ───────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-semibold text-text-primary">Compliance Export</h2>
        <p className="text-sm text-text-muted mt-1">
          Generate auditable compliance artifacts from the receipt DAG
        </p>
      </div>

      {/* Metrics Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricCard label="Formats Available" value={formats.length} />
        <MetricCard label="Total Exports" value={historyTotal} />
        <MetricCard
          label="Last Export"
          value={history.length > 0 ? formatTimestamp(history[0]?.generated_at) : 'None'}
        />
        <MetricCard
          label="Chain Status"
          value={
            history.length > 0
              ? history[0]?.chain_integrity === 'CHAIN_INTACT'
                ? 'Intact'
                : 'Anomaly'
              : '--'
          }
        />
      </div>

      {/* Export Generator */}
      <div className="bg-surface-card border border-border-default rounded-lg p-5">
        <h3 className="text-sm font-semibold text-text-primary mb-4">Generate Export</h3>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Format Selector */}
          <div>
            <label className="block text-xs text-text-muted mb-1.5">Export Format</label>
            <div className="space-y-1.5">
              {(['PDF', 'SOC2_JSON', 'ISO27001_JSON', 'GDPR_JSON'] as const).map((fmt) => (
                <button
                  key={fmt}
                  onClick={() => setSelectedFormat(fmt)}
                  className={`w-full flex items-center gap-2.5 px-3 py-2 rounded text-left text-sm transition-colors ${
                    selectedFormat === fmt
                      ? 'bg-accent-primary/10 text-accent-primary border border-accent-primary/30'
                      : 'bg-surface-input text-text-secondary border border-border-default hover:border-border-strong'
                  }`}
                >
                  <span className="text-[10px] font-mono font-bold bg-surface-card-elevated px-1.5 py-0.5 rounded">
                    {FORMAT_ICONS[fmt]}
                  </span>
                  <span>{FORMAT_LABELS[fmt]}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Period Selector */}
          <div>
            <label className="block text-xs text-text-muted mb-1.5">Export Period</label>
            <div className="space-y-2">
              <div>
                <label className="block text-[10px] text-text-muted mb-0.5">Start</label>
                <input
                  type="date"
                  value={periodStart}
                  onChange={(e) => setPeriodStart(e.target.value)}
                  className="w-full px-3 py-1.5 rounded bg-surface-input border border-border-default text-text-primary text-sm focus:outline-none focus:border-accent-primary"
                />
              </div>
              <div>
                <label className="block text-[10px] text-text-muted mb-0.5">End</label>
                <input
                  type="date"
                  value={periodEnd}
                  onChange={(e) => setPeriodEnd(e.target.value)}
                  className="w-full px-3 py-1.5 rounded bg-surface-input border border-border-default text-text-primary text-sm focus:outline-none focus:border-accent-primary"
                />
              </div>
              <div className="flex flex-wrap gap-1">
                {PERIOD_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    onClick={() => applyPreset(p)}
                    className="px-2 py-0.5 text-[10px] rounded bg-surface-card-elevated text-text-muted hover:text-text-primary border border-border-default hover:border-border-strong transition-colors"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Scope & Config */}
          <div>
            <label className="block text-xs text-text-muted mb-1.5">Scope & Configuration</label>
            <div className="space-y-2">
              <div>
                <label className="block text-[10px] text-text-muted mb-0.5">
                  Quest ID (optional)
                </label>
                <input
                  type="text"
                  value={questId}
                  onChange={(e) => setQuestId(e.target.value)}
                  placeholder="All workflows"
                  className="w-full px-3 py-1.5 rounded bg-surface-input border border-border-default text-text-primary text-sm placeholder:text-text-muted focus:outline-none focus:border-accent-primary"
                />
              </div>
              <div>
                <label className="block text-[10px] text-text-muted mb-0.5">
                  Anomaly Threshold (blocked/24h)
                </label>
                <input
                  type="number"
                  value={anomalyThreshold}
                  onChange={(e) => setAnomalyThreshold(parseInt(e.target.value, 10) || 5)}
                  min={1}
                  max={100}
                  className="w-full px-3 py-1.5 rounded bg-surface-input border border-border-default text-text-primary text-sm focus:outline-none focus:border-accent-primary"
                />
              </div>
            </div>
          </div>

          {/* Generate Button */}
          <div className="flex flex-col justify-end">
            <button
              onClick={handleGenerate}
              disabled={generating || !periodStart || !periodEnd}
              className={`w-full py-3 rounded font-semibold text-sm transition-colors ${
                generating
                  ? 'bg-accent-primary/50 text-text-primary cursor-wait'
                  : 'bg-accent-primary text-white hover:bg-accent-primary/90'
              }`}
            >
              {generating ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                      fill="none"
                    />
                    <path
                      className="opacity-75"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                    />
                  </svg>
                  Generating...
                </span>
              ) : (
                'Generate Export'
              )}
            </button>
          </div>
        </div>

        {/* Result / Error */}
        {exportError && (
          <div className="mt-4 p-3 rounded bg-status-error/10 border border-status-error/20 text-status-error text-sm">
            {exportError}
          </div>
        )}

        {lastResult && lastResult.success && (
          <div className="mt-4 p-4 rounded bg-status-success/5 border border-status-success/20">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-semibold text-status-success">Export Generated</span>
              {chainBadge(lastResult.chain_integrity)}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs text-text-muted">
              <div>
                <span className="block text-text-secondary font-medium">Receipts</span>
                {lastResult.receipt_count.toLocaleString()}
              </div>
              <div>
                <span className="block text-text-secondary font-medium">Duration</span>
                {lastResult.export_duration_ms.toFixed(0)}ms
              </div>
              <div>
                <span className="block text-text-secondary font-medium">SHA-256</span>
                <span className="font-mono">{lastResult.output_sha256.slice(0, 16)}...</span>
              </div>
              <div>
                <a
                  href={getExportDownloadUrl(lastResult.export_id)}
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded bg-accent-primary text-white text-xs font-medium hover:bg-accent-primary/90 transition-colors"
                  download
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
                  </svg>
                  Download
                </a>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Export History */}
      <div className="bg-surface-card border border-border-default rounded-lg">
        <div className="px-5 py-3 border-b border-border-default flex items-center justify-between">
          <h3 className="text-sm font-semibold text-text-primary">
            Export History
            {historyTotal > 0 && (
              <span className="ml-2 text-xs text-text-muted font-normal">({historyTotal})</span>
            )}
          </h3>
        </div>

        {history.length === 0 ? (
          <div className="p-8">
            <EmptyState
              title="No exports yet"
              description="Generate your first compliance export above."
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border-default text-left text-xs text-text-muted">
                  <th className="px-4 py-2 font-medium">Format</th>
                  <th className="px-4 py-2 font-medium">Period</th>
                  <th className="px-4 py-2 font-medium">Receipts</th>
                  <th className="px-4 py-2 font-medium">Chain</th>
                  <th className="px-4 py-2 font-medium">Generated</th>
                  <th className="px-4 py-2 font-medium">Duration</th>
                  <th className="px-4 py-2 font-medium">SHA-256</th>
                  <th className="px-4 py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {history.map((entry) => {
                  const vr = verifyResults[entry.export_id]
                  return (
                    <tr
                      key={entry.export_id}
                      className="border-b border-border-default/50 hover:bg-surface-card-elevated/30 transition-colors"
                    >
                      <td className="px-4 py-2.5">
                        <span className="inline-flex items-center gap-1.5">
                          <span className="text-[10px] font-mono font-bold bg-surface-card-elevated px-1.5 py-0.5 rounded">
                            {FORMAT_ICONS[entry.export_format] || entry.export_format}
                          </span>
                          <span className="text-text-secondary">
                            {FORMAT_LABELS[entry.export_format] || entry.export_format}
                          </span>
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-text-muted font-mono text-xs">
                        {entry.period_start?.slice(0, 10)} — {entry.period_end?.slice(0, 10)}
                      </td>
                      <td className="px-4 py-2.5 text-text-secondary">
                        {entry.receipt_count.toLocaleString()}
                      </td>
                      <td className="px-4 py-2.5">{chainBadge(entry.chain_integrity)}</td>
                      <td className="px-4 py-2.5 text-text-muted text-xs">
                        {formatTimestamp(entry.generated_at)}
                      </td>
                      <td className="px-4 py-2.5 text-text-muted text-xs">
                        {entry.export_duration_ms.toFixed(0)}ms
                      </td>
                      <td className="px-4 py-2.5">
                        <span
                          className="font-mono text-xs text-text-muted cursor-help"
                          title={entry.output_sha256}
                        >
                          {entry.output_sha256?.slice(0, 12)}...
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-1.5">
                          {/* Download */}
                          <a
                            href={getExportDownloadUrl(entry.export_id)}
                            className="px-2 py-1 text-xs rounded bg-surface-input border border-border-default text-text-secondary hover:text-text-primary hover:border-border-strong transition-colors"
                            download
                            title="Download"
                          >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
                            </svg>
                          </a>

                          {/* Verify */}
                          <button
                            onClick={() => handleVerify(entry.export_id)}
                            disabled={verifying === entry.export_id}
                            className={`px-2 py-1 text-xs rounded border transition-colors ${
                              vr
                                ? vr.verified
                                  ? 'bg-status-success/10 border-status-success/30 text-status-success'
                                  : 'bg-status-error/10 border-status-error/30 text-status-error'
                                : 'bg-surface-input border-border-default text-text-secondary hover:text-text-primary hover:border-border-strong'
                            }`}
                            title={
                              vr
                                ? vr.verified
                                  ? 'Hash verified'
                                  : vr.reason || 'Hash mismatch'
                                : 'Verify SHA-256 integrity'
                            }
                          >
                            {verifying === entry.export_id ? (
                              <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                              </svg>
                            ) : vr?.verified ? (
                              '\u2713'
                            ) : vr ? (
                              '\u2717'
                            ) : (
                              '\u2714'
                            )}
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
