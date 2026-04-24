import { createContext, useContext, useCallback, useState, useMemo } from 'react'
import type { ReactNode } from 'react'
import type { WsEvent } from '@/hooks/useWebSocket'
import type {
  ActionCardData,
  ChatProgressState,
  ChatRunState,
  ChatRunStatus,
  ToolFlowStep,
  ToolFlowState,
} from '@/types/api'

// ------------------------------------------------------------------
// Context value shape
// ------------------------------------------------------------------

interface LiveEventsContextValue {
  /** Map of questId -> ToolFlowState for all active/recent quests */
  toolFlowState: Map<string, ToolFlowState>
  /** Pending action cards waiting for user decision */
  pendingActionCards: ActionCardData[]
  /** Latest chat/governance progress event */
  chatProgress: ChatProgressState | null
  /** Async Command Center runs keyed by run_id */
  chatRuns: Map<string, ChatRunState>
  /** Call this from WarRoomShell to route incoming WS events */
  handleLiveEvent: (event: WsEvent) => void
  /** Mark an action card as resolved locally (optimistic update) */
  resolveCard: (cardId: string, buttonId: string, channel?: string, confirmed?: boolean) => void
  /** Add or update an async chat run from a POST acknowledgement */
  trackChatRun: (run: ChatRunState) => void
}

const LiveEventsContext = createContext<LiveEventsContextValue | null>(null)

const CHAT_RUN_STATUS_ORDER: Record<ChatRunStatus, number> = {
  queued: 0,
  running: 1,
  blocked: 2,
  cancelled: 3,
  failed: 4,
  succeeded: 5,
}

function chatRunTimestamp(run: ChatRunState): number {
  const candidates = [
    run.updated_at,
    run.completed_at,
    run.started_at,
    run.created_at,
  ]
  for (const candidate of candidates) {
    if (!candidate) continue
    const parsed = Date.parse(candidate)
    if (!Number.isNaN(parsed)) return parsed
  }
  return 0
}

function mergeChatRunState(existing: ChatRunState | undefined, incoming: ChatRunState): ChatRunState {
  if (!existing) return incoming

  const existingTimestamp = chatRunTimestamp(existing)
  const incomingTimestamp = chatRunTimestamp(incoming)

  if (incomingTimestamp > existingTimestamp) {
    return { ...existing, ...incoming }
  }
  if (incomingTimestamp < existingTimestamp) {
    return existing
  }

  const existingStatusOrder = CHAT_RUN_STATUS_ORDER[existing.status] ?? 0
  const incomingStatusOrder = CHAT_RUN_STATUS_ORDER[incoming.status] ?? 0
  if (incomingStatusOrder < existingStatusOrder) {
    return existing
  }

  return { ...existing, ...incoming }
}

// ------------------------------------------------------------------
// Provider
// ------------------------------------------------------------------

interface LiveEventsProviderProps {
  children: ReactNode
}

export function LiveEventsProvider({ children }: LiveEventsProviderProps) {
  const [toolFlowState, setToolFlowState] = useState<Map<string, ToolFlowState>>(new Map())
  const [pendingActionCards, setPendingActionCards] = useState<ActionCardData[]>([])
  const [chatProgress, setChatProgress] = useState<ChatProgressState | null>(null)
  const [chatRuns, setChatRuns] = useState<Map<string, ChatRunState>>(new Map())

  // ── Tool Flow event handlers ────────────────────────────────

  const handleToolFlowEvent = useCallback((event: WsEvent) => {
    const payload = event.payload

    switch (event.type) {
      case 'toolflow.quest_started': {
        const questId = payload.quest_id as string
        const maxIterations = (payload.max_iterations as number) || 10
        setToolFlowState((prev) => {
          const next = new Map(prev)
          next.set(questId, {
            questId,
            steps: [],
            status: 'running',
            currentIteration: 0,
            maxIterations,
          })
          return next
        })
        break
      }

      case 'toolflow.tool_call_started': {
        const questId = payload.quest_id as string
        const iteration = (payload.iteration as number) || 0
        const toolName = (payload.tool_name as string) || 'unknown'
        const step: ToolFlowStep = {
          iteration,
          toolName,
          status: 'running',
          timestamp: event.timestamp || Date.now() / 1000,
        }
        setToolFlowState((prev) => {
          const next = new Map(prev)
          const existing = next.get(questId)
          if (existing) {
            next.set(questId, {
              ...existing,
              currentIteration: iteration,
              steps: [...existing.steps, step],
            })
          }
          return next
        })
        break
      }

      case 'toolflow.iteration_started': {
        const questId = payload.quest_id as string
        const iteration = (payload.iteration as number) || 0
        setToolFlowState((prev) => {
          const next = new Map(prev)
          const existing = next.get(questId)
          if (existing) {
            next.set(questId, {
              ...existing,
              currentIteration: iteration,
            })
          }
          return next
        })
        break
      }

      case 'toolflow.tool_call_completed': {
        const questId = payload.quest_id as string
        const iteration = (payload.iteration as number) || 0
        const result = (payload.tool_result as string) || ''
        const stepStatus: ToolFlowStep['status'] =
          result.startsWith('FAIL') || result.startsWith('EXCEPTION') || result.startsWith('REJECTED')
            ? 'failed'
            : result.startsWith('ESCALATED')
              ? 'blocked'
              : 'success'
        const summary =
          (payload.tool_outputs_summary as string) ||
          (payload.output_summary as string) ||
          result ||
          undefined
        setToolFlowState((prev) => {
          const next = new Map(prev)
          const existing = next.get(questId)
          if (existing) {
            const steps = existing.steps.map((s) =>
              s.iteration === iteration && s.status === 'running'
                ? { ...s, status: stepStatus, outputSummary: summary }
                : s,
            )
            next.set(questId, { ...existing, steps })
          }
          return next
        })
        break
      }

      case 'toolflow.tool_call_blocked': {
        const questId = payload.quest_id as string
        const iteration = (payload.iteration as number) || 0
        const approvalId = payload.approval_id as string | undefined
        const reason =
          (payload.reason as string) ||
          (approvalId ? `Awaiting approval ${approvalId}` : 'Awaiting Commander approval')
        setToolFlowState((prev) => {
          const next = new Map(prev)
          const existing = next.get(questId)
          if (existing) {
            const steps = existing.steps.map((s) =>
              s.iteration === iteration && s.status === 'running'
                ? { ...s, status: 'blocked' as const, outputSummary: reason }
                : s,
            )
            next.set(questId, { ...existing, steps })
          }
          return next
        })
        break
      }

      case 'toolflow.quest_blocked': {
        const questId = payload.quest_id as string
        setToolFlowState((prev) => {
          const next = new Map(prev)
          const existing = next.get(questId)
          if (existing) {
            const steps = existing.steps.map((s) =>
              s.status === 'running' ? { ...s, status: 'blocked' as const } : s,
            )
            next.set(questId, { ...existing, steps, status: 'blocked' })
          }
          return next
        })
        break
      }

      case 'toolflow.quest_completed': {
        const questId = payload.quest_id as string
        setToolFlowState((prev) => {
          const next = new Map(prev)
          const existing = next.get(questId)
          if (existing) {
            // Mark any remaining running steps as success
            const steps = existing.steps.map((s) =>
              s.status === 'running' ? { ...s, status: 'success' as const } : s,
            )
            next.set(questId, { ...existing, steps, status: 'completed' })
          }
          return next
        })
        break
      }

      case 'toolflow.quest_failed': {
        const questId = payload.quest_id as string
        setToolFlowState((prev) => {
          const next = new Map(prev)
          const existing = next.get(questId)
          if (existing) {
            const steps = existing.steps.map((s) =>
              s.status === 'running' ? { ...s, status: 'failed' as const } : s,
            )
            next.set(questId, { ...existing, steps, status: 'failed' })
          }
          return next
        })
        break
      }
    }
  }, [])

  // ── Action Card event handlers ──────────────────────────────

  const handleActionCardEvent = useCallback((event: WsEvent) => {
    const payload = event.payload

    if (event.type === 'actioncard_presented') {
      const card: ActionCardData = {
        cardId: payload.card_id as string,
        cardType: (payload.card_type as ActionCardData['cardType']) || 'info',
        questId: (payload.quest_id as string | null | undefined) ?? null,
        sourceSystem: (payload.source_system as string | undefined) || undefined,
        sourceItemId: (payload.source_item_id as string | undefined) || undefined,
        title: (payload.title as string) || '',
        description: (payload.description as string) || '',
        buttons: (payload.buttons as ActionCardData['buttons']) || [],
        resolved: false,
        presentedAt: event.timestamp || Date.now() / 1000,
      }
      setPendingActionCards((prev) => {
        // Avoid duplicates
        if (prev.some((c) => c.cardId === card.cardId)) return prev
        return [...prev, card]
      })
    }

    if (event.type === 'actioncard_resolved') {
      const cardId = payload.card_id as string
      const resolvedAction =
        (payload.resolved_action as string) || (payload.button_id as string) || undefined
      const resolvedChannel =
        (payload.resolved_channel as string) || (payload.channel as string) || undefined
      const questId = (payload.quest_id as string | null | undefined) ?? null
      setPendingActionCards((prev) =>
        prev.map((c) =>
          c.cardId === cardId
            ? {
                ...c,
                questId: c.questId ?? questId,
                resolved: true,
                resolvedAction,
                resolvedChannel,
                resolutionConfirmed: true,
                resolvedAt: event.timestamp || Date.now() / 1000,
              }
            : c,
        ),
      )
    }
  }, [])

  // ── Unified handler for WarRoomShell ────────────────────────

  const handleLiveEvent = useCallback(
    (event: WsEvent) => {
      if (event.type.startsWith('toolflow.')) {
        handleToolFlowEvent(event)
      } else if (event.type.startsWith('actioncard_')) {
        handleActionCardEvent(event)
      } else if (event.type === 'chat.progress') {
        const severity = event.payload.severity as ChatProgressState['severity'] | undefined
        setChatProgress({
          questId: (event.payload.quest_id as string | null | undefined) ?? null,
          phase: (event.payload.phase as string) || 'processing',
          message: (event.payload.message as string) || 'Processing request',
          timestamp: event.timestamp || Date.now() / 1000,
          severity,
          degraded: event.payload.degraded === true,
          degradedReason: (event.payload.degraded_reason as string | undefined) || undefined,
          waitReason: (event.payload.wait_reason as string | undefined) || undefined,
        })
      } else if (event.type.startsWith('chat.run_')) {
        const run = event.payload as unknown as ChatRunState
        if (run.run_id) {
          setChatRuns((prev) => {
            const next = new Map(prev)
            next.set(run.run_id, mergeChatRunState(prev.get(run.run_id), run))
            return next
          })
        }
      }
    },
    [handleToolFlowEvent, handleActionCardEvent],
  )

  // ── Optimistic resolve (called from ActionCardComponent) ────

  const resolveCard = useCallback((cardId: string, buttonId: string, channel?: string, confirmed = true) => {
    setPendingActionCards((prev) =>
      prev.map((c) =>
        c.cardId === cardId
          ? {
              ...c,
              resolved: true,
              resolvedAction: buttonId,
              resolvedChannel: channel || 'war_room',
              resolutionConfirmed: confirmed,
              resolvedAt: Date.now() / 1000,
            }
          : c,
      ),
    )
  }, [])

  const trackChatRun = useCallback((run: ChatRunState) => {
    if (!run.run_id) return
    setChatRuns((prev) => {
      const next = new Map(prev)
      // The POST /chat/async acknowledgement can arrive after a very fast
      // websocket completion event. Preserve the newest known run state.
      next.set(run.run_id, mergeChatRunState(prev.get(run.run_id), run))
      return next
    })
  }, [])

  const value = useMemo<LiveEventsContextValue>(
    () => ({
      toolFlowState,
      pendingActionCards,
      chatProgress,
      chatRuns,
      handleLiveEvent,
      resolveCard,
      trackChatRun,
    }),
    [toolFlowState, pendingActionCards, chatProgress, chatRuns, handleLiveEvent, resolveCard, trackChatRun],
  )

  return <LiveEventsContext.Provider value={value}>{children}</LiveEventsContext.Provider>
}

// ------------------------------------------------------------------
// Hook
// ------------------------------------------------------------------

export function useLiveEvents(): LiveEventsContextValue {
  const ctx = useContext(LiveEventsContext)
  if (!ctx) {
    throw new Error('useLiveEvents must be used within a <LiveEventsProvider>')
  }
  return ctx
}
