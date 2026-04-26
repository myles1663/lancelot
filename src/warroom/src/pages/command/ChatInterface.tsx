import { Fragment, useState, useRef, useEffect, useCallback } from 'react'
import { cancelChatRun, fetchChatRuns, resumeWorkItem, retryChatRun, sendMessageAsync, sendMessageWithFiles } from '@/api'
import { fetchChatHistory } from '@/api/chat'
import { resolveActionCard } from '@/api/actioncards'
import { ChatMessage } from './ChatMessage'
import { ToolFlowIndicator } from '@/components/ToolFlowIndicator'
import { ActionCardComponent } from '@/components/ActionCardComponent'
import { useLiveEvents } from '@/contexts/LiveEventsContext'
import type { ActionCardData, ChatRunProgressEvent, ChatRunReceiptProof, ChatRunState } from '@/types/api'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  crusaderMode?: boolean
  filesCount?: number
  receiptProof?: ChatRunReceiptProof | null
}

function formatProgressPhase(phase: string): string {
  return phase.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function fallbackProgressMessage(elapsedSeconds: number): string {
  if (elapsedSeconds >= 60) {
    return 'No progress event yet. Check active work, container logs, or model latency if this continues.'
  }
  if (elapsedSeconds >= 25) {
    return 'Still queued or running; Lancelot may be waiting on model or tool latency.'
  }
  if (elapsedSeconds >= 10) {
    return 'Waiting for the next governance progress event.'
  }
  return 'Submitting to the governed execution queue.'
}

function ProgressSpinner() {
  return (
    <svg
      className="w-3.5 h-3.5 animate-spin text-accent-primary"
      viewBox="0 0 16 16"
      fill="none"
    >
      <circle
        cx="8"
        cy="8"
        r="6"
        stroke="currentColor"
        strokeWidth="2"
        strokeOpacity="0.25"
      />
      <path
        d="M14 8a6 6 0 0 0-6-6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  )
}

function isResumeCommand(text: string): boolean {
  const normalized = text.trim().toLowerCase()
  return ['continue', 'proceed', 'resume', 'carry on', 'go for it'].includes(normalized)
}

function isApprovedApprovalCard(card: ActionCardData): boolean {
  const action = (card.resolvedAction || '').toLowerCase()
  return (
    card.cardType === 'approval' &&
    card.sourceSystem === 'governance' &&
    card.resolved &&
    action === 'approve'
  )
}

function ApprovalResumePrompt({
  cardId,
  questId,
  disabled,
  onResume,
}: {
  cardId: string
  questId?: string | null
  disabled: boolean
  onResume: (cardId: string, questId?: string | null) => void
}) {
  return (
    <div className="bg-surface-card border border-border-default border-l-4 border-l-state-healthy rounded-lg px-4 py-3 my-2 animate-slide-in">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-medium text-text-primary">
            Approval granted. Lancelot is ready to continue the approved work.
          </p>
          <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
            Resume only continues the scope approved in the ActionCard above.
          </p>
        </div>
        <button
          type="button"
          onClick={() => onResume(cardId, questId)}
          disabled={disabled}
          className="shrink-0 px-3 py-1.5 text-xs font-medium rounded-md bg-accent-primary text-white hover:bg-accent-primary/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {disabled ? 'Continuing...' : 'Continue'}
        </button>
      </div>
    </div>
  )
}

function formatRunStatus(status: string): string {
  return status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatElapsed(ms?: number | null): string {
  if (ms === null || ms === undefined) return '--'
  if (ms < 1000) return `${ms}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(seconds >= 10 ? 0 : 1)}s`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.floor(seconds % 60)
  return `${minutes}m ${remainder}s`
}

function parseTimestampSeconds(value?: string | null): number | null {
  if (!value) return null
  const parsed = Date.parse(value)
  if (Number.isNaN(parsed)) return null
  return parsed / 1000
}

function phaseTimingSummary(run: ChatRunState): string {
  const timings = Object.entries(run.phase_timings_ms || {})
  if (timings.length === 0) {
    return `${formatElapsed(run.total_elapsed_ms)} elapsed`
  }
  return timings
    .slice(-3)
    .map(([phase, ms]) => `${phase.replace(/_/g, ' ')} ${formatElapsed(ms)}`)
    .join(' / ')
}

function latestDegradedProgress(run: ChatRunState) {
  const events = [...(run.progress_events || [])].reverse()
  return events.find((event) => event.degraded || event.severity === 'warning' || event.severity === 'error')
}

function latestProgressEvent(run: ChatRunState): ChatRunProgressEvent | undefined {
  const events = run.progress_events || []
  return events.length > 0 ? events[events.length - 1] : undefined
}

function recentProgressEvents(run: ChatRunState): ChatRunProgressEvent[] {
  return [...(run.progress_events || [])].slice(-3)
}

function waitReasonForRun(run: ChatRunState, progress?: ChatRunProgressEvent): string {
  if (progress?.wait_reason) return progress.wait_reason
  if (run.phase === 'waiting_worker_slot') return 'worker_slot'
  if (run.phase === 'provider_call') return 'provider_call'
  if (run.phase === 'finalization') return 'finalization'
  if (run.phase === 'approval' || run.status === 'blocked') return 'approval'
  return ''
}

function waitReasonHeadline(status: string, waitReason: string): string {
  if (status === 'queued' && waitReason === 'worker_slot') {
    return 'Waiting for governed worker slot.'
  }
  if (status === 'running' && waitReason === 'provider_call') {
    return 'Waiting on governed provider response.'
  }
  if (status === 'running' && waitReason === 'finalization') {
    return 'Finalizing response and execution proof.'
  }
  if (status === 'blocked' || waitReason === 'approval') {
    return 'Waiting for Commander approval.'
  }
  return ''
}

function waitReasonBadge(waitReason?: string): string {
  if (!waitReason) return 'WAITING'
  const labels: Record<string, string> = {
    worker_slot: 'WORKER',
    provider_call: 'PROVIDER',
    finalization: 'FINALIZE',
    approval: 'APPROVAL',
    tool_execution: 'TOOL',
  }
  return labels[waitReason] || waitReason.replace(/_/g, ' ').toUpperCase()
}

function progressAgeSeconds(run: ChatRunState, nowSeconds: number): number | null {
  const latestProgress = latestProgressEvent(run)
  const timestamp =
    parseTimestampSeconds(latestProgress?.at) ??
    parseTimestampSeconds(run.updated_at) ??
    parseTimestampSeconds(run.created_at)
  if (timestamp === null) return null
  return Math.max(0, Math.floor(nowSeconds - timestamp))
}

function staleProgressMessage(run: ChatRunState, nowSeconds: number): string {
  if (run.status !== 'queued' && run.status !== 'running') return ''
  const ageSeconds = progressAgeSeconds(run, nowSeconds)
  if (ageSeconds === null || ageSeconds < 60) return ''
  const phase = formatProgressPhase(run.phase || latestProgressEvent(run)?.phase || 'processing')
  return `No new progress for ${formatElapsed(ageSeconds * 1000)}. Last phase: ${phase}.`
}

function isActiveChatRun(run: ChatRunState): boolean {
  return run.status === 'queued' || run.status === 'running'
}

function ChatRunIndicator({
  run,
  nowSeconds,
  actionPending,
  onCancel,
  onRetry,
}: {
  run: ChatRunState
  nowSeconds: number
  actionPending: boolean
  onCancel: (runId: string) => void
  onRetry: (runId: string) => void
}) {
  const statusStyles: Record<string, string> = {
    queued: 'bg-state-warning/15 text-state-warning',
    running: 'bg-accent-primary/15 text-accent-primary',
    blocked: 'bg-state-warning/15 text-state-warning',
    succeeded: 'bg-state-healthy/15 text-state-healthy',
    failed: 'bg-state-error/15 text-state-error',
    cancelled: 'bg-state-inactive/15 text-text-muted',
  }
  const isActive = run.status === 'queued' || run.status === 'running'
  const canRetry = run.status === 'failed' || run.status === 'cancelled'
  const latestProgress = latestProgressEvent(run)
  const waitReason = waitReasonForRun(run, latestProgress)
  const waitHeadline = waitReasonHeadline(run.status, waitReason)
  const degradedProgress = latestDegradedProgress(run)
  const hasDegradedProgress = Boolean(degradedProgress)
  const slowProgressMessage = staleProgressMessage(run, nowSeconds)
  const hasSlowProgress = Boolean(slowProgressMessage)
  const statusHeadline =
    waitHeadline ||
    (run.status === 'queued'
      ? run.retry_of_run_id
        ? 'Retry queued. Waiting for the previous cancelled work to drain.'
        : 'Queued for governed execution.'
      : run.status === 'running'
        ? 'Lancelot is executing this request.'
        : run.status === 'blocked'
          ? 'Execution paused for approval.'
          : run.status === 'failed'
            ? 'Execution failed.'
            : run.status === 'cancelled'
              ? 'Execution cancelled.'
              : 'Execution completed.')
  const progressMessage = run.last_progress_message || latestProgress?.message || ''
  const fallbackSupplementalMessage =
    (run.status === 'queued' && run.retry_of_run_id
      ? 'The previous run was cancelled cooperatively. The retry will start when the worker slot frees.'
      : run.status === 'queued'
        ? 'Waiting for a governed worker slot.'
        : '')
  const supplementalMessage =
    progressMessage && progressMessage !== statusHeadline
      ? progressMessage
      : fallbackSupplementalMessage
  const recentEvents = recentProgressEvents(run)
  return (
    <div className={`bg-surface-card border rounded-lg px-4 py-3 my-2 animate-slide-in ${
      hasDegradedProgress || hasSlowProgress ? 'border-state-degraded/50' : 'border-border-default'
    }`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          {isActive && <ProgressSpinner />}
          <div className="min-w-0">
            <p className="text-xs font-medium text-text-primary truncate">
              {statusHeadline}
            </p>
            <p className="text-[10px] font-mono uppercase tracking-wider text-text-muted truncate">
              {run.phase || run.run_id.slice(0, 8)} - {run.run_id.slice(0, 8)}
            </p>
            {supplementalMessage && (
              <p className="mt-1 text-[10px] text-text-muted truncate">
                {supplementalMessage}
              </p>
            )}
            <p className="mt-0.5 text-[10px] font-mono text-text-muted truncate">
              {phaseTimingSummary(run)}
            </p>
            {recentEvents.length > 0 && (
              <div className="mt-2 space-y-1">
                {recentEvents.map((event, index) => (
                  <div
                    key={`${event.at}-${event.phase}-${index}`}
                    className="grid grid-cols-[5.5rem_minmax(0,1fr)_3.5rem] gap-2 text-[10px] leading-relaxed"
                  >
                    <span className="font-mono uppercase text-text-muted truncate">
                      {formatProgressPhase(event.phase)}
                    </span>
                    <span className="text-text-secondary truncate">
                      {event.message}
                    </span>
                    <span className="font-mono text-right text-text-muted">
                      {formatElapsed(event.elapsed_ms)}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {degradedProgress && (
              <div className="mt-2 border-l-2 border-state-degraded pl-2">
                <p className="text-[10px] font-medium text-state-degraded">
                  {degradedProgress.message}
                </p>
                {degradedProgress.degraded_reason && (
                  <p className="mt-0.5 text-[10px] text-text-muted truncate">
                    {degradedProgress.degraded_reason}
                  </p>
                )}
              </div>
            )}
            {slowProgressMessage && !degradedProgress && (
              <div className="mt-2 border-l-2 border-state-degraded pl-2">
                <p className="text-[10px] font-medium text-state-degraded">
                  {slowProgressMessage}
                </p>
              </div>
            )}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          {isActive && (
            <button
              type="button"
              onClick={() => onCancel(run.run_id)}
              disabled={actionPending}
              className="px-2 py-1 text-[10px] font-medium rounded-md border border-border-default text-text-secondary hover:text-state-error hover:border-state-error/60 disabled:opacity-50 disabled:cursor-not-allowed"
              title="Cancel this async run"
            >
              {actionPending ? 'Stopping' : 'Cancel'}
            </button>
          )}
          {canRetry && (
            <button
              type="button"
              onClick={() => onRetry(run.run_id)}
              disabled={actionPending}
              className="px-2 py-1 text-[10px] font-medium rounded-md border border-border-default text-text-secondary hover:text-accent-primary hover:border-accent-primary/60 disabled:opacity-50 disabled:cursor-not-allowed"
              title="Retry this governed command"
            >
              {actionPending ? 'Retrying' : 'Retry'}
            </button>
          )}
          <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono font-semibold uppercase tracking-wider ${statusStyles[run.status] || statusStyles.running}`}>
            {formatRunStatus(run.status)}
          </span>
          {hasDegradedProgress && (
            <span className="text-[9px] px-1.5 py-0.5 rounded font-mono font-semibold uppercase tracking-wider bg-state-degraded/15 text-state-degraded">
              DEGRADED
            </span>
          )}
          {isActive && waitReason && (
            <span className="text-[9px] px-1.5 py-0.5 rounded font-mono font-semibold uppercase tracking-wider bg-accent-primary/15 text-accent-primary">
              {waitReasonBadge(waitReason)}
            </span>
          )}
          {hasSlowProgress && (
            <span className="text-[9px] px-1.5 py-0.5 rounded font-mono font-semibold uppercase tracking-wider bg-state-degraded/15 text-state-degraded">
              SLOW
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

export function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [sending, setSending] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [nowSeconds, setNowSeconds] = useState(() => Math.floor(Date.now() / 1000))
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [resumedApprovalCardIds, setResumedApprovalCardIds] = useState<Set<string>>(new Set())
  const [renderedChatRunIds, setRenderedChatRunIds] = useState<Set<string>>(new Set())
  const [runActionPendingId, setRunActionPendingId] = useState<string | null>(null)
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const sendStartedAtRef = useRef(0)
  const {
    toolFlowState,
    pendingActionCards,
    resolveCard,
    chatProgress,
    chatRuns,
    trackChatRun,
  } = useLiveEvents()
  const chatRunsRef = useRef(chatRuns)
  const activeChatRuns = Array.from(chatRuns.values()).filter(isActiveChatRun)
  const visibleRunCards = Array.from(chatRuns.values()).filter(
    (run) =>
      run.status === 'queued' ||
      run.status === 'running' ||
      run.status === 'failed' ||
      run.status === 'cancelled',
  )
  const isExecutionActive = activeChatRuns.length > 0
  const activeChatProgress =
    chatProgress && (sending || isExecutionActive) && chatProgress.timestamp >= sendStartedAtRef.current - 1
      ? chatProgress
      : null

  const scrollToBottom = useCallback(() => {
    const container = messagesContainerRef.current
    if (!container) return
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
  }, [])

  useEffect(scrollToBottom, [messages, scrollToBottom])

  // Also scroll when live execution state updates.
  useEffect(scrollToBottom, [toolFlowState, pendingActionCards, activeChatProgress, chatRuns, scrollToBottom])

  useEffect(() => {
    if (!sending && !isExecutionActive) {
      setElapsedSeconds(0)
      return
    }

    const timer = window.setInterval(() => {
      const now = Math.floor(Date.now() / 1000)
      setNowSeconds(now)
      setElapsedSeconds(sendStartedAtRef.current ? now - sendStartedAtRef.current : 0)
    }, 1000)

    return () => window.clearInterval(timer)
  }, [sending, isExecutionActive])

  useEffect(() => {
    chatRunsRef.current = chatRuns
  }, [chatRuns])

  const reconcilePersistedChatRuns = useCallback(async () => {
    const payload = await fetchChatRuns(25)
    const knownRunIds = new Set(chatRunsRef.current.keys())
    payload.runs.forEach((run) => {
      if (isActiveChatRun(run) || knownRunIds.has(run.run_id)) {
        trackChatRun(run)
      }
    })
  }, [trackChatRun])

  useEffect(() => {
    let cancelled = false

    const reconcile = () => {
      reconcilePersistedChatRuns().catch(() => {
        // WebSocket events remain the primary path; reconciliation is best-effort.
      })
    }

    if (!cancelled) reconcile()
    const timer = window.setInterval(() => {
      if (!cancelled) reconcile()
    }, 10000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [reconcilePersistedChatRuns])

  // Load conversation history from backend on mount
  useEffect(() => {
    if (historyLoaded) return
    fetchChatHistory(50)
      .then((data) => {
        if (data.messages.length > 0) {
          const loaded: Message[] = data.messages.map((m, i) => ({
            id: `history-${i}`,
            role: m.role as 'user' | 'assistant',
            content: m.content,
            timestamp: m.timestamp
              ? new Date(m.timestamp * 1000).toLocaleTimeString('en-US', { hour12: false })
              : '',
          }))
          setMessages(loaded)
        }
      })
      .catch(() => {
        // Silently ignore — fresh session
      })
      .finally(() => setHistoryLoaded(true))
  }, [historyLoaded])

  const timestamp = () => new Date().toLocaleTimeString('en-US', { hour12: false })

  useEffect(() => {
    const terminalRuns = Array.from(chatRuns.values()).filter(
      (run) =>
        ['blocked', 'cancelled', 'failed', 'succeeded'].includes(run.status) &&
        !renderedChatRunIds.has(run.run_id),
    )

    if (terminalRuns.length === 0) return

    setRenderedChatRunIds((prev) => {
      const next = new Set(prev)
      terminalRuns.forEach((run) => next.add(run.run_id))
      return next
    })

    const terminalMessages: Message[] = terminalRuns.map((run) => {
      let content = run.response
      if (run.status === 'failed') {
        content = `Error: ${run.error || run.response || 'Async chat run failed.'}`
      } else if (run.status === 'cancelled') {
        content = 'Run cancelled before completion.'
      } else if (!content) {
        content = `Run ${formatRunStatus(run.status).toLowerCase()}.`
      }

      return {
        id: run.request_id || run.run_id,
        role: 'assistant',
        content,
        timestamp: new Date().toLocaleTimeString('en-US', { hour12: false }),
        crusaderMode: run.crusader_mode,
        receiptProof: run.receipt_proof,
      }
    })

    setMessages((prev) => [...prev, ...terminalMessages])
  }, [chatRuns, renderedChatRunIds])

  const markApprovedCardsResumed = useCallback((cardIds?: string[]) => {
    setResumedApprovalCardIds((prev) => {
      const next = new Set(prev)
      const ids =
        cardIds ??
        pendingActionCards
          .filter((card) => isApprovedApprovalCard(card))
          .map((card) => card.cardId)
      ids.forEach((cardId) => next.add(cardId))
      return next
    })
  }, [pendingActionCards])

  const sendChatMessage = useCallback(
    async (text: string, attachedFiles: File[] = []): Promise<boolean> => {
      if (!text && attachedFiles.length === 0) return false

      const userMsg: Message = {
        id: crypto.randomUUID(),
        role: 'user',
        content: text || `[${attachedFiles.length} file(s) attached]`,
        timestamp: timestamp(),
        filesCount: attachedFiles.length,
      }
      setMessages((prev) => [...prev, userMsg])
      setInput('')
      sendStartedAtRef.current = Date.now() / 1000
      setElapsedSeconds(0)
      setSending(true)

      try {
        if (attachedFiles.length === 0) {
          const result = await sendMessageAsync(text)
          trackChatRun(result.run)
          return true
        }

        const result = await sendMessageWithFiles(text, attachedFiles)

        const assistantMsg: Message = {
          id: result.request_id,
          role: 'assistant',
          content: result.response,
          timestamp: timestamp(),
          crusaderMode: result.crusader_mode,
        }
        setMessages((prev) => [...prev, assistantMsg])
        return true
      } catch (err) {
        const errorMsg: Message = {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: `Error: ${err instanceof Error ? err.message : 'Unknown error'}`,
          timestamp: timestamp(),
        }
        setMessages((prev) => [...prev, errorMsg])
        return false
      } finally {
        setSending(false)
        setFiles([])
      }
    },
    [trackChatRun],
  )

  const handleSend = async () => {
    const text = input.trim()
    const attachedFiles = files
    const sent = await sendChatMessage(text, attachedFiles)
    if (sent && isResumeCommand(text)) {
      markApprovedCardsResumed()
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // Resolve through the backend before updating the local card state.
  const handleActionCardAction = useCallback(
    async (cardId: string, buttonId: string) => {
      await resolveActionCard(cardId, buttonId)
      resolveCard(cardId, buttonId, 'warroom', true)
    },
    [resolveCard],
  )

  const appendAssistantError = useCallback((content: string) => {
    const errorMsg: Message = {
      id: crypto.randomUUID(),
      role: 'assistant',
      content,
      timestamp: timestamp(),
    }
    setMessages((prev) => [...prev, errorMsg])
  }, [])

  const handleCancelChatRun = useCallback(
    async (runId: string) => {
      if (runActionPendingId) return
      setRunActionPendingId(runId)
      try {
        const result = await cancelChatRun(runId)
        trackChatRun(result.run)
      } catch (err) {
        appendAssistantError(`Error: ${err instanceof Error ? err.message : 'Unable to cancel run'}`)
      } finally {
        setRunActionPendingId(null)
      }
    },
    [appendAssistantError, runActionPendingId, trackChatRun],
  )

  const handleRetryChatRun = useCallback(
    async (runId: string) => {
      if (runActionPendingId || isExecutionActive) return
      setRunActionPendingId(runId)
      try {
        const result = await retryChatRun(runId)
        trackChatRun(result.run)
      } catch (err) {
        appendAssistantError(`Error: ${err instanceof Error ? err.message : 'Unable to retry run'}`)
      } finally {
        setRunActionPendingId(null)
      }
    },
    [appendAssistantError, isExecutionActive, runActionPendingId, trackChatRun],
  )

  // Get action cards that are not yet resolved
  const visibleActionCards = pendingActionCards
  const resumableApprovalCardIds = new Set(
    visibleActionCards
      .filter((card) => (
        isApprovedApprovalCard(card) &&
        card.resolutionConfirmed !== false &&
        !resumedApprovalCardIds.has(card.cardId)
      ))
      .map((card) => card.cardId),
  )
  const pendingApprovalQuestIds = new Set(
    visibleActionCards
      .filter((card) => !card.resolved && card.questId)
      .map((card) => card.questId as string),
  )

  const handleResumeApprovedWork = useCallback(
    async (cardId: string, questId?: string | null) => {
      if (sending || isExecutionActive || runActionPendingId) return
      setRunActionPendingId(cardId)
      try {
        if (questId) {
          const result = await resumeWorkItem(questId)
          trackChatRun(result.run)
          markApprovedCardsResumed([cardId])
          return
        }
        const sent = await sendChatMessage('continue')
        if (sent) {
          markApprovedCardsResumed([cardId])
        }
      } catch (err) {
        appendAssistantError(`Error: ${err instanceof Error ? err.message : 'Unable to resume approved work'}`)
      } finally {
        setRunActionPendingId(null)
      }
    },
    [
      appendAssistantError,
      isExecutionActive,
      markApprovedCardsResumed,
      runActionPendingId,
      sendChatMessage,
      sending,
      trackChatRun,
    ],
  )

  // Running flows should show while the request is active. Blocked flows stay
  // visible beside their approval card so the operator can see why chat paused.
  const visibleFlows = Array.from(toolFlowState.values()).filter((flow) => {
    if (flow.status === 'running') return sending || isExecutionActive
    if (flow.status === 'blocked') return sending || isExecutionActive || pendingApprovalQuestIds.has(flow.questId)
    return false
  })

  return (
    <section className="bg-surface-card border border-border-default rounded-lg flex flex-col h-[clamp(24rem,65vh,37.5rem)]">
      <div className="px-4 py-3 border-b border-border-default shrink-0">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
          Command Interface
        </h3>
      </div>

      {/* Messages */}
      <div
        ref={messagesContainerRef}
        className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-4 space-y-1"
      >
        {messages.length === 0 && !sending && (
          <div className="flex items-center justify-center h-full text-text-muted text-sm">
            Issue a command to Lancelot
          </div>
        )}
        {messages.map((msg) => (
          <ChatMessage key={msg.id} {...msg} />
        ))}

        {/* Tool Flow Indicators — shown while sending and agentic loop is active */}
        {sending && activeChatProgress && (
          <div className={`bg-surface-card border rounded-lg px-4 py-3 my-2 animate-slide-in ${
            activeChatProgress.degraded ? 'border-state-degraded/50' : 'border-border-default'
          }`}>
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 min-w-0">
                <ProgressSpinner />
                <div className="min-w-0">
                  <p className={`text-xs font-medium truncate ${
                    activeChatProgress.degraded ? 'text-state-degraded' : 'text-text-primary'
                  }`}>
                    {activeChatProgress.message}
                  </p>
                  <p className="text-[10px] font-mono uppercase tracking-wider text-text-muted truncate">
                    {formatProgressPhase(activeChatProgress.phase)}
                  </p>
                  {activeChatProgress.degradedReason && (
                    <p className="mt-1 text-[10px] text-text-muted truncate">
                      {activeChatProgress.degradedReason}
                    </p>
                  )}
                </div>
              </div>
              <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono font-semibold uppercase tracking-wider ${
                activeChatProgress.degraded
                  ? 'bg-state-degraded/15 text-state-degraded'
                  : activeChatProgress.waitReason
                    ? 'bg-accent-primary/15 text-accent-primary'
                    : 'bg-accent-primary/20 text-accent-primary'
              }`}>
                {activeChatProgress.degraded
                  ? 'DEGRADED'
                  : activeChatProgress.waitReason
                    ? waitReasonBadge(activeChatProgress.waitReason)
                    : 'LIVE'}
              </span>
            </div>
          </div>
        )}

        {visibleRunCards.map((run) => (
          <ChatRunIndicator
            key={run.run_id}
            run={run}
            nowSeconds={nowSeconds}
            actionPending={runActionPendingId === run.run_id}
            onCancel={handleCancelChatRun}
            onRetry={handleRetryChatRun}
          />
        ))}

        {visibleFlows.map((flow) => (
          <ToolFlowIndicator
            key={flow.questId}
            questId={flow.questId}
            steps={flow.steps}
            currentIteration={flow.currentIteration}
            maxIterations={flow.maxIterations}
            status={flow.status}
          />
        ))}

        {/* Fallback sending indicator when no tool flow events are streaming */}
        {sending && visibleFlows.length === 0 && !activeChatProgress && (
          <div className="bg-surface-card border border-border-default rounded-lg px-4 py-3 my-2 animate-slide-in flex items-center gap-2">
            <ProgressSpinner />
            <div className="min-w-0">
              <p className="text-xs font-medium text-text-primary">
                {fallbackProgressMessage(elapsedSeconds)}
              </p>
              <p className="text-[10px] font-mono uppercase tracking-wider text-text-muted">
                {elapsedSeconds}s elapsed
              </p>
            </div>
          </div>
        )}

        {/* Action Cards — rendered inline */}
        {visibleActionCards.map((card) => (
          <Fragment key={card.cardId}>
            <ActionCardComponent
              cardId={card.cardId}
              cardType={card.cardType}
              title={card.title}
              description={card.description}
              buttons={card.buttons}
              resolved={card.resolved}
              resolvedAction={card.resolvedAction}
              resolvedChannel={card.resolvedChannel}
              onAction={handleActionCardAction}
            />
            {resumableApprovalCardIds.has(card.cardId) && (
              <ApprovalResumePrompt
                cardId={card.cardId}
                questId={card.questId}
                disabled={sending || isExecutionActive || runActionPendingId === card.cardId}
                onResume={handleResumeApprovedWork}
              />
            )}
          </Fragment>
        ))}
      </div>

      {/* File chips */}
      {files.length > 0 && (
        <div className="px-4 py-2 border-t border-border-default flex flex-wrap gap-2 shrink-0">
          {files.map((f, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 px-2 py-1 bg-surface-input rounded text-xs text-text-secondary"
            >
              {f.name}
              <button
                onClick={() => setFiles((prev) => prev.filter((_, idx) => idx !== i))}
                className="text-text-muted hover:text-state-error"
              >
                x
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="p-3 border-t border-border-default flex gap-2 shrink-0">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) setFiles(Array.from(e.target.files))
          }}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          className="p-2 text-text-muted hover:text-text-primary transition-colors"
          title="Attach files"
        >
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <path
              d="M15.2 8.46L9.06 14.6C8.3 15.36 7.28 15.79 6.22 15.79C5.16 15.79 4.14 15.36 3.38 14.6C2.62 13.84 2.19 12.82 2.19 11.76C2.19 10.7 2.62 9.68 3.38 8.92L9.52 2.78C10.02 2.28 10.7 2 11.41 2C12.12 2 12.8 2.28 13.3 2.78C13.8 3.28 14.08 3.96 14.08 4.67C14.08 5.38 13.8 6.06 13.3 6.56L7.15 12.7C6.9 12.95 6.56 13.09 6.21 13.09C5.86 13.09 5.52 12.95 5.27 12.7C5.02 12.45 4.88 12.11 4.88 11.76C4.88 11.41 5.02 11.07 5.27 10.82L10.94 5.16"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Issue command to Lancelot..."
          rows={1}
          className="flex-1 bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-active resize-none"
        />
        <button
          onClick={handleSend}
          disabled={sending || isExecutionActive || (!input.trim() && files.length === 0)}
          className="px-4 py-2 bg-accent-primary text-white text-sm font-medium rounded-md hover:bg-accent-primary/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isExecutionActive ? 'Running...' : sending ? 'Sending...' : 'Send'}
        </button>
      </div>
    </section>
  )
}
