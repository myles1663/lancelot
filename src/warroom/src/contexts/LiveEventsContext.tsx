import { createContext, useContext, useCallback, useState, useMemo } from 'react'
import type { ReactNode } from 'react'
import type { WsEvent } from '@/hooks/useWebSocket'
import type { ToolFlowStep, ToolFlowState, ActionCardData } from '@/types/api'

// ------------------------------------------------------------------
// Context value shape
// ------------------------------------------------------------------

interface LiveEventsContextValue {
  /** Map of questId -> ToolFlowState for all active/recent quests */
  toolFlowState: Map<string, ToolFlowState>
  /** Pending action cards waiting for user decision */
  pendingActionCards: ActionCardData[]
  /** Call this from WarRoomShell to route incoming WS events */
  handleLiveEvent: (event: WsEvent) => void
  /** Mark an action card as resolved locally (optimistic update) */
  resolveCard: (cardId: string, buttonId: string, channel?: string) => void
}

const LiveEventsContext = createContext<LiveEventsContextValue | null>(null)

// ------------------------------------------------------------------
// Provider
// ------------------------------------------------------------------

interface LiveEventsProviderProps {
  children: ReactNode
}

export function LiveEventsProvider({ children }: LiveEventsProviderProps) {
  const [toolFlowState, setToolFlowState] = useState<Map<string, ToolFlowState>>(new Map())
  const [pendingActionCards, setPendingActionCards] = useState<ActionCardData[]>([])

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

      case 'toolflow.tool_call_completed': {
        const questId = payload.quest_id as string
        const iteration = (payload.iteration as number) || 0
        const summary = (payload.output_summary as string) || undefined
        setToolFlowState((prev) => {
          const next = new Map(prev)
          const existing = next.get(questId)
          if (existing) {
            const steps = existing.steps.map((s) =>
              s.iteration === iteration && s.status === 'running'
                ? { ...s, status: 'success' as const, outputSummary: summary }
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
        const reason = (payload.reason as string) || 'Blocked by policy'
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
      const resolvedAction = (payload.resolved_action as string) || undefined
      const resolvedChannel = (payload.resolved_channel as string) || undefined
      setPendingActionCards((prev) =>
        prev.map((c) =>
          c.cardId === cardId
            ? {
                ...c,
                resolved: true,
                resolvedAction,
                resolvedChannel,
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
      }
    },
    [handleToolFlowEvent, handleActionCardEvent],
  )

  // ── Optimistic resolve (called from ActionCardComponent) ────

  const resolveCard = useCallback((cardId: string, buttonId: string, channel?: string) => {
    setPendingActionCards((prev) =>
      prev.map((c) =>
        c.cardId === cardId
          ? {
              ...c,
              resolved: true,
              resolvedAction: buttonId,
              resolvedChannel: channel || 'war_room',
              resolvedAt: Date.now() / 1000,
            }
          : c,
      ),
    )
  }, [])

  const value = useMemo<LiveEventsContextValue>(
    () => ({
      toolFlowState,
      pendingActionCards,
      handleLiveEvent,
      resolveCard,
    }),
    [toolFlowState, pendingActionCards, handleLiveEvent, resolveCard],
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
