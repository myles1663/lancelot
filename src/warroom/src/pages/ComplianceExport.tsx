import { useState, useCallback } from 'react'
import {
  Activity,
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  Download,
  FileCheck2,
  FileText,
  Fingerprint,
  History,
  RefreshCw,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react'
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
import { StatusDot, EmptyState } from '@/components'
import { formatTimestamp } from '@/utils/dateFormat'

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

const EXPORT_FORMATS = ['PDF', 'SOC2_JSON', 'ISO27001_JSON', 'GDPR_JSON'] as const

function chainBadge(status: string) {
  const intact = status === 'CHAIN_INTACT'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-mono ${
        intact
          ? 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy'
          : 'border-state-error/30 bg-state-error/10 text-state-error'
      }`}
    >
      <StatusDot state={intact ? 'healthy' : 'error'} />
      {intact ? 'CHAIN INTACT' : status}
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

const PERIOD_PRESETS = [
  { label: 'Last 7 days', start: () => daysAgoISO(7), end: todayISO },
  { label: 'Last 30 days', start: () => daysAgoISO(30), end: todayISO },
  { label: 'Last 90 days', start: () => daysAgoISO(90), end: todayISO },
  { label: 'Year to date', start: () => `${new Date().getFullYear()}-01-01`, end: todayISO },
]

type ComplianceTileTone = 'accent' | 'healthy' | 'warning' | 'error' | 'muted'

const complianceTileToneClass: Record<ComplianceTileTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  error: 'border-state-error/30 bg-state-error/10 text-state-error',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function ComplianceTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: ComplianceTileTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${complianceTileToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 break-words text-2xl font-semibold leading-tight text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

function OperatorNote({
  icon: Icon,
  title,
  children,
  tone = 'accent',
}: {
  icon: LucideIcon
  title: string
  children: string
  tone?: 'accent' | 'healthy' | 'warning'
}) {
  const iconClass =
    tone === 'healthy'
      ? 'text-state-healthy'
      : tone === 'warning'
        ? 'text-state-warning'
        : 'text-accent-primary'

  return (
    <div className="rounded-lg border border-border-default bg-surface-card-elevated px-4 py-3">
      <div className="flex items-center gap-2">
        <Icon className={`h-4 w-4 ${iconClass}`} aria-hidden="true" />
        <h4 className="text-sm font-semibold text-text-primary">{title}</h4>
      </div>
      <p className="mt-2 text-xs leading-5 text-text-muted">{children}</p>
    </div>
  )
}

function formatDuration(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms.toFixed(0)}ms`
}

export function ComplianceExport() {
  usePageTitle('Compliance Export')

  const { data: formatsData } = usePolling({ fetcher: fetchExportFormats, interval: 60000 })
  const { data: historyData, refetch: refetchHistory } = usePolling({
    fetcher: fetchExportHistory,
    interval: 15000,
  })

  const [selectedFormat, setSelectedFormat] = useState('PDF')
  const [periodStart, setPeriodStart] = useState(() => daysAgoISO(30))
  const [periodEnd, setPeriodEnd] = useState(todayISO)
  const [questId, setQuestId] = useState('')
  const [anomalyThreshold, setAnomalyThreshold] = useState(5)

  const [generating, setGenerating] = useState(false)
  const [lastResult, setLastResult] = useState<ExportResponse | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)

  const [verifying, setVerifying] = useState<string | null>(null)
  const [verifyResults, setVerifyResults] = useState<Record<string, VerifyResult>>({})

  const formats: ExportFormat[] = formatsData?.formats ?? []
  const history: ExportHistoryEntry[] = historyData?.exports ?? []
  const historyTotal = historyData?.total ?? 0
  const latestExport = history[0]
  const availableFormats = formats.length > 0 ? formats.filter((format) => format.available).length : EXPORT_FORMATS.length
  const selectedFormatInfo = formats.find((format) => format.id === selectedFormat)
  const selectedFormatDescription = selectedFormatInfo?.description ?? 'Generate an operator-ready compliance artifact.'
  const selectedFormatAvailable = selectedFormatInfo?.available ?? true
  const latestChainIntact = latestExport?.chain_integrity === 'CHAIN_INTACT'
  const verifiedCount = Object.values(verifyResults).filter((result) => result.verified).length

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

  return (
    <div className="space-y-6 pt-1 md:pt-0">
      <div className="rounded-lg border border-border-default bg-surface-card p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2">
              <FileCheck2 className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <div className="text-[11px] font-semibold uppercase tracking-wider text-accent-primary">
                Compliance Evidence
              </div>
            </div>
            <h2 className="mt-2 text-2xl font-semibold text-text-primary">Compliance Export</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Generate auditable evidence packages from the receipt DAG, verify export hashes, and keep a retrievable export trail.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono text-text-muted sm:flex sm:flex-wrap">
            <span className="rounded border border-border-default bg-surface-input px-2 py-1">
              formats {availableFormats}
            </span>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1">
              exports {historyTotal}
            </span>
            <span
              className={`rounded border px-2 py-1 ${
                latestExport
                  ? latestChainIntact
                    ? 'border-state-healthy/40 bg-state-healthy/10 text-state-healthy'
                    : 'border-state-error/40 bg-state-error/10 text-state-error'
                  : 'border-border-default bg-surface-input'
              }`}
            >
              {latestExport ? (latestChainIntact ? 'chain intact' : 'chain anomaly') : 'no exports'}
            </span>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-5">
          <ComplianceTile
            label="Formats Available"
            value={availableFormats}
            detail={`${EXPORT_FORMATS.length} supported evidence package types.`}
            tone="accent"
          />
          <ComplianceTile
            label="Total Exports"
            value={historyTotal}
            detail="Generated artifacts retained in export history."
          />
          <ComplianceTile
            label="Last Export"
            value={latestExport ? formatTimestamp(latestExport.generated_at) : 'None'}
            detail={latestExport ? FORMAT_LABELS[latestExport.export_format] ?? latestExport.export_format : 'No evidence packages generated yet.'}
            tone={latestExport ? 'healthy' : 'muted'}
          />
          <ComplianceTile
            label="Latest Chain"
            value={latestExport ? (latestChainIntact ? 'Intact' : 'Anomaly') : '--'}
            detail={latestExport ? 'Receipt ancestry status for the newest export.' : 'Generate an export to establish chain status.'}
            tone={latestExport ? (latestChainIntact ? 'healthy' : 'error') : 'muted'}
          />
          <ComplianceTile
            label="Verified Here"
            value={verifiedCount}
            detail="Exports verified during this operator session."
            tone={verifiedCount > 0 ? 'healthy' : 'muted'}
          />
        </div>
      </div>

      <div className="rounded-lg border border-border-default bg-surface-card p-5">
        <div className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <h3 className="text-sm font-semibold text-text-primary">Generate Evidence Package</h3>
            </div>
            <p className="mt-1 text-xs leading-5 text-text-muted">
              Select the compliance format, evidence period, optional quest scope, and anomaly threshold.
            </p>
          </div>
          <span
            className={`inline-flex w-fit items-center gap-2 rounded border px-2.5 py-1 text-xs font-mono ${
              selectedFormatAvailable
                ? 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary'
                : 'border-state-error/30 bg-state-error/10 text-state-error'
            }`}
          >
            <span>{FORMAT_ICONS[selectedFormat] ?? selectedFormat}</span>
            <span className="opacity-60">|</span>
            <span>{selectedFormatAvailable ? 'available' : 'unavailable'}</span>
          </span>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.95fr)]">
          <div className="min-w-0">
            <label className="block text-xs text-text-muted mb-1.5">Export Format</label>
            <div className="space-y-1.5">
              {EXPORT_FORMATS.map((fmt) => {
                const formatInfo = formats.find((format) => format.id === fmt)
                const available = formatInfo?.available ?? true
                return (
                  <button
                    key={fmt}
                    onClick={() => setSelectedFormat(fmt)}
                    className={`w-full min-w-0 rounded border px-3 py-2 text-left text-sm transition-colors ${
                      selectedFormat === fmt
                        ? 'border-accent-primary/40 bg-accent-primary/10 text-accent-primary'
                        : 'border-border-default bg-surface-input text-text-secondary hover:border-border-strong hover:text-text-primary'
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-2.5">
                      <span className="shrink-0 rounded bg-surface-card-elevated px-1.5 py-0.5 text-[10px] font-mono font-bold">
                        {FORMAT_ICONS[fmt]}
                      </span>
                      <span className="min-w-0 truncate">{FORMAT_LABELS[fmt]}</span>
                    </span>
                    {!available && (
                      <span className="mt-1 block text-[10px] font-mono text-state-error">
                        unavailable
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
            <p className="mt-2 text-xs leading-5 text-text-muted">{selectedFormatDescription}</p>
          </div>

          <div className="min-w-0">
            <label className="block text-xs text-text-muted mb-1.5">Export Period</label>
            <div className="space-y-2">
              <div>
                <label className="block text-[10px] text-text-muted mb-0.5">Start</label>
                <input
                  type="date"
                  value={periodStart}
                  onChange={(e) => setPeriodStart(e.target.value)}
                  className="block w-full min-w-0 max-w-full appearance-none rounded border border-border-default bg-surface-input px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-primary"
                />
              </div>
              <div>
                <label className="block text-[10px] text-text-muted mb-0.5">End</label>
                <input
                  type="date"
                  value={periodEnd}
                  onChange={(e) => setPeriodEnd(e.target.value)}
                  className="block w-full min-w-0 max-w-full appearance-none rounded border border-border-default bg-surface-input px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-primary"
                />
              </div>
              <div className="flex flex-wrap gap-1">
                {PERIOD_PRESETS.map((preset) => (
                  <button
                    key={preset.label}
                    onClick={() => applyPreset(preset)}
                    className="rounded border border-border-default bg-surface-card-elevated px-2 py-0.5 text-[10px] text-text-muted transition-colors hover:border-border-strong hover:text-text-primary"
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="min-w-0">
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
                  className="block w-full min-w-0 max-w-full rounded border border-border-default bg-surface-input px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-primary"
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
                  className="block w-full min-w-0 max-w-full rounded border border-border-default bg-surface-input px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-primary"
                />
              </div>
            </div>
          </div>

          <div className="flex min-w-0 flex-col justify-end">
            <button
              onClick={handleGenerate}
              disabled={generating || !periodStart || !periodEnd || !selectedFormatAvailable}
              className={`inline-flex w-full items-center justify-center gap-2 rounded px-4 py-3 text-sm font-semibold transition-colors ${
                generating
                  ? 'bg-accent-primary/50 text-text-primary cursor-wait'
                  : 'bg-accent-primary text-white hover:bg-accent-primary/90 disabled:cursor-not-allowed disabled:opacity-50'
              }`}
            >
              {generating ? (
                <>
                  <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" />
                  Generating...
                </>
              ) : (
                <>
                  <FileCheck2 className="h-4 w-4" aria-hidden="true" />
                  Generate Export
                </>
              )}
            </button>
          </div>
        </div>

        {exportError && (
          <div className="mt-4 flex items-start gap-2 rounded border border-state-error/30 bg-state-error/10 p-3 text-sm text-state-error">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{exportError}</span>
          </div>
        )}

        {lastResult && lastResult.success && (
          <div className="mt-4 rounded border border-state-healthy/30 bg-state-healthy/10 p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <span className="inline-flex items-center gap-2 text-sm font-semibold text-state-healthy">
                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                Export Generated
              </span>
              {chainBadge(lastResult.chain_integrity)}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3 text-xs text-text-muted md:grid-cols-4">
              <div>
                <span className="block font-medium text-text-secondary">Receipts</span>
                {lastResult.receipt_count.toLocaleString()}
              </div>
              <div>
                <span className="block font-medium text-text-secondary">Duration</span>
                {formatDuration(lastResult.export_duration_ms)}
              </div>
              <div>
                <span className="block font-medium text-text-secondary">SHA-256</span>
                <span className="font-mono">{lastResult.output_sha256.slice(0, 16)}...</span>
              </div>
              <div>
                <a
                  href={getExportDownloadUrl(lastResult.export_id)}
                  className="inline-flex items-center gap-1.5 rounded bg-accent-primary px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-primary/90"
                  download
                >
                  <Download className="h-3.5 w-3.5" aria-hidden="true" />
                  Download
                </a>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="rounded-lg border border-border-default bg-surface-card">
        <div className="flex flex-col gap-3 border-b border-border-default px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <History className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <h3 className="text-sm font-semibold text-text-primary">Export History</h3>
            </div>
            <p className="mt-1 text-xs leading-5 text-text-muted">
              Download prior evidence packages or verify their SHA-256 hash before sharing.
            </p>
          </div>
          {historyTotal > 0 && (
            <span className="w-fit rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
              {historyTotal} exports
            </span>
          )}
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
                      className="border-b border-border-default/50 transition-colors hover:bg-surface-card-elevated/30"
                    >
                      <td className="px-4 py-2.5">
                        <span className="inline-flex items-center gap-1.5">
                          <span className="rounded bg-surface-card-elevated px-1.5 py-0.5 text-[10px] font-mono font-bold">
                            {FORMAT_ICONS[entry.export_format] || entry.export_format}
                          </span>
                          <span className="text-text-secondary">
                            {FORMAT_LABELS[entry.export_format] || entry.export_format}
                          </span>
                        </span>
                      </td>
                      <td className="px-4 py-2.5 font-mono text-xs text-text-muted">
                        {entry.period_start?.slice(0, 10)} - {entry.period_end?.slice(0, 10)}
                      </td>
                      <td className="px-4 py-2.5 text-text-secondary">
                        {entry.receipt_count.toLocaleString()}
                      </td>
                      <td className="px-4 py-2.5">{chainBadge(entry.chain_integrity)}</td>
                      <td className="px-4 py-2.5 text-xs text-text-muted">
                        {formatTimestamp(entry.generated_at)}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-text-muted">
                        {formatDuration(entry.export_duration_ms)}
                      </td>
                      <td className="px-4 py-2.5">
                        <span
                          className="cursor-help font-mono text-xs text-text-muted"
                          title={entry.output_sha256}
                        >
                          {entry.output_sha256?.slice(0, 12)}...
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-1.5">
                          <a
                            href={getExportDownloadUrl(entry.export_id)}
                            className="inline-flex h-7 w-7 items-center justify-center rounded border border-border-default bg-surface-input text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
                            download
                            title="Download export"
                          >
                            <Download className="h-3.5 w-3.5" aria-hidden="true" />
                          </a>

                          <button
                            onClick={() => handleVerify(entry.export_id)}
                            disabled={verifying === entry.export_id}
                            className={`inline-flex h-7 w-7 items-center justify-center rounded border transition-colors ${
                              vr
                                ? vr.verified
                                  ? 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy'
                                  : 'border-state-error/30 bg-state-error/10 text-state-error'
                                : 'border-border-default bg-surface-input text-text-secondary hover:border-border-strong hover:text-text-primary'
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
                              <RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                            ) : vr?.verified ? (
                              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                            ) : vr ? (
                              <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                            ) : (
                              <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
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

      <div className="rounded-lg border border-border-default bg-surface-card p-5">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-accent-primary" aria-hidden="true" />
          <h3 className="text-sm font-semibold text-text-primary">Operator Flow</h3>
        </div>
        <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
          <OperatorNote icon={CalendarDays} title="1. Define Scope">
            Choose the export format, date range, and optional quest filter in the generator.
          </OperatorNote>
          <OperatorNote icon={FileCheck2} title="2. Generate Export" tone="healthy">
            Use Generate Export to produce the evidence package and capture chain status.
          </OperatorNote>
          <OperatorNote icon={Fingerprint} title="3. Download Or Verify">
            Use the action buttons in Export History to download the artifact or verify its SHA-256 hash.
          </OperatorNote>
        </div>
      </div>
    </div>
  )
}
