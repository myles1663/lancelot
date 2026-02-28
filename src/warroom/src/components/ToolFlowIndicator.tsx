import type { ToolFlowStep } from '@/types/api'

// ------------------------------------------------------------------
// ToolFlowIndicator — animated step-by-step progress for agentic loop
// Rendered inline in chat while a quest is running
// ------------------------------------------------------------------

interface ToolFlowIndicatorProps {
  questId: string
  steps: ToolFlowStep[]
  currentIteration: number
  maxIterations: number
  status: 'running' | 'completed' | 'failed'
}

// ── Status icons (inline SVG to avoid external deps) ──────────

function SpinnerIcon() {
  return (
    <svg
      className="w-3.5 h-3.5 text-accent-primary animate-spin"
      viewBox="0 0 16 16"
      fill="none"
    >
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.25" />
      <path
        d="M14 8a6 6 0 0 0-6-6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg className="w-3.5 h-3.5 text-state-healthy" viewBox="0 0 16 16" fill="none">
      <path
        d="M3.5 8.5L6.5 11.5L12.5 4.5"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function FailIcon() {
  return (
    <svg className="w-3.5 h-3.5 text-state-error" viewBox="0 0 16 16" fill="none">
      <path
        d="M4 4L12 12M12 4L4 12"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  )
}

function BlockedIcon() {
  return (
    <svg className="w-3.5 h-3.5 text-state-warning" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5" />
      <path d="M5 8H11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

function StepIcon({ status }: { status: ToolFlowStep['status'] }) {
  switch (status) {
    case 'running':
      return <SpinnerIcon />
    case 'success':
      return <CheckIcon />
    case 'failed':
      return <FailIcon />
    case 'blocked':
      return <BlockedIcon />
  }
}

// ── Quest-level status badge ──────────────────────────────────

function QuestStatusBadge({ status }: { status: ToolFlowIndicatorProps['status'] }) {
  const styles: Record<string, string> = {
    running: 'bg-accent-primary/20 text-accent-primary',
    completed: 'bg-state-healthy/20 text-state-healthy',
    failed: 'bg-state-error/20 text-state-error',
  }

  const labels: Record<string, string> = {
    running: 'EXECUTING',
    completed: 'COMPLETE',
    failed: 'FAILED',
  }

  return (
    <span
      className={`text-[9px] px-1.5 py-0.5 rounded font-mono font-semibold uppercase tracking-wider ${styles[status]}`}
    >
      {labels[status]}
    </span>
  )
}

// ── Main component ────────────────────────────────────────────

export function ToolFlowIndicator({
  steps,
  currentIteration,
  maxIterations,
  status,
}: ToolFlowIndicatorProps) {
  return (
    <div className="bg-surface-card border border-border-default rounded-lg px-4 py-3 my-2 animate-slide-in">
      {/* Header row */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">
            Tool Flow
          </span>
          <QuestStatusBadge status={status} />
        </div>
        <span className="text-[10px] font-mono text-text-muted">
          {currentIteration}/{maxIterations}
        </span>
      </div>

      {/* Progress bar */}
      <div className="w-full h-1 rounded-full bg-surface-input overflow-hidden mb-3">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            status === 'failed' ? 'bg-state-error' : 'bg-accent-primary'
          }`}
          style={{
            width: `${Math.min(100, Math.max(0, (currentIteration / maxIterations) * 100))}%`,
          }}
        />
      </div>

      {/* Step list */}
      {steps.length > 0 && (
        <div className="space-y-1.5">
          {steps.map((step, idx) => (
            <div key={`${step.iteration}-${idx}`} className="flex items-start gap-2">
              <div className="flex-shrink-0 mt-0.5">
                <StepIcon status={step.status} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-text-primary truncate">
                    {step.toolName}
                  </span>
                  <span className="text-[9px] font-mono text-text-muted">
                    #{step.iteration}
                  </span>
                </div>
                {step.outputSummary && (
                  <p className="text-[11px] text-text-muted mt-0.5 truncate">
                    {step.outputSummary}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Empty running state */}
      {steps.length === 0 && status === 'running' && (
        <div className="flex items-center gap-2 text-text-muted">
          <SpinnerIcon />
          <span className="text-xs">Initializing quest...</span>
        </div>
      )}
    </div>
  )
}
