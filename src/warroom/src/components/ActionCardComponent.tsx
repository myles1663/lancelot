import { useState } from 'react'
import type { ActionCardType, ActionCardButton, ActionCardButtonStyle } from '@/types/api'

// ------------------------------------------------------------------
// ActionCardComponent — interactive button card for approvals/actions
// Rendered inline in chat when actioncard_presented events arrive
// ------------------------------------------------------------------

interface ActionCardComponentProps {
  cardId: string
  cardType: ActionCardType
  title: string
  description: string
  buttons: ActionCardButton[]
  resolved: boolean
  resolvedAction?: string
  resolvedChannel?: string
  onAction: (cardId: string, buttonId: string) => void
}

// ── Card type badge ───────────────────────────────────────────

const CARD_TYPE_STYLES: Record<ActionCardType, { bg: string; text: string; label: string }> = {
  approval: { bg: 'bg-state-warning/15', text: 'text-state-warning', label: 'APPROVAL' },
  confirmation: { bg: 'bg-accent-primary/15', text: 'text-accent-primary', label: 'CONFIRM' },
  choice: { bg: 'bg-accent-secondary/15', text: 'text-accent-secondary', label: 'CHOICE' },
  info: { bg: 'bg-state-inactive/15', text: 'text-text-secondary', label: 'INFO' },
}

function CardTypeBadge({ cardType }: { cardType: ActionCardType }) {
  const config = CARD_TYPE_STYLES[cardType]
  return (
    <span
      className={`text-[9px] px-1.5 py-0.5 rounded font-mono font-semibold uppercase tracking-wider ${config.bg} ${config.text}`}
    >
      {config.label}
    </span>
  )
}

// ── Card type icons ───────────────────────────────────────────

function CardIcon({ cardType }: { cardType: ActionCardType }) {
  const iconClass = 'w-4 h-4 flex-shrink-0'

  switch (cardType) {
    case 'approval':
      return (
        <svg className={`${iconClass} text-state-warning`} viewBox="0 0 16 16" fill="none">
          <path
            d="M8 2L2 14H14L8 2Z"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
          <path d="M8 6V9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <circle cx="8" cy="11.5" r="0.75" fill="currentColor" />
        </svg>
      )
    case 'confirmation':
      return (
        <svg className={`${iconClass} text-accent-primary`} viewBox="0 0 16 16" fill="none">
          <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5" />
          <path
            d="M5.5 8L7.5 10L10.5 6"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )
    case 'choice':
      return (
        <svg className={`${iconClass} text-accent-secondary`} viewBox="0 0 16 16" fill="none">
          <path
            d="M8 2V6M8 6L4 10M8 6L12 10"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <circle cx="4" cy="12" r="1.5" stroke="currentColor" strokeWidth="1.5" />
          <circle cx="12" cy="12" r="1.5" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      )
    case 'info':
      return (
        <svg className={`${iconClass} text-text-secondary`} viewBox="0 0 16 16" fill="none">
          <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5" />
          <path d="M8 7V11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <circle cx="8" cy="5" r="0.75" fill="currentColor" />
        </svg>
      )
  }
}

// ── Button styling ────────────────────────────────────────────

const BUTTON_STYLES: Record<ActionCardButtonStyle, string> = {
  primary:
    'bg-accent-primary hover:bg-accent-primary/80 text-white',
  danger:
    'bg-state-error hover:bg-state-error/80 text-white',
  secondary:
    'bg-surface-input hover:bg-surface-card-elevated text-text-secondary border border-border-default',
}

// ── Main component ────────────────────────────────────────────

export function ActionCardComponent({
  cardId,
  cardType,
  title,
  description,
  buttons,
  resolved,
  resolvedAction,
  resolvedChannel,
  onAction,
}: ActionCardComponentProps) {
  const [submitting, setSubmitting] = useState<string | null>(null)

  const handleClick = (buttonId: string) => {
    if (resolved || submitting) return
    setSubmitting(buttonId)
    onAction(cardId, buttonId)
  }

  // Find the label of the resolved button
  const resolvedLabel = resolvedAction
    ? buttons.find((b) => b.id === resolvedAction)?.label || resolvedAction
    : undefined

  // Card border color varies by type
  const borderAccent: Record<ActionCardType, string> = {
    approval: 'border-l-state-warning',
    confirmation: 'border-l-accent-primary',
    choice: 'border-l-accent-secondary',
    info: 'border-l-state-inactive',
  }

  return (
    <div
      className={`bg-surface-card border border-border-default ${borderAccent[cardType]} border-l-4 rounded-lg px-4 py-3 my-2 animate-slide-in ${
        resolved ? 'opacity-70' : ''
      }`}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-2">
        <CardIcon cardType={cardType} />
        <span className="text-sm font-medium text-text-primary flex-1">{title}</span>
        <CardTypeBadge cardType={cardType} />
      </div>

      {/* Description */}
      {description && (
        <p className="text-xs text-text-secondary mb-3 leading-relaxed">{description}</p>
      )}

      {/* Buttons */}
      {!resolved && (
        <div className="flex flex-wrap gap-2">
          {buttons.map((btn) => (
            <button
              key={btn.id}
              onClick={() => handleClick(btn.id)}
              disabled={submitting !== null}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${BUTTON_STYLES[btn.style]}`}
            >
              {submitting === btn.id ? 'Sending...' : btn.label}
            </button>
          ))}
        </div>
      )}

      {/* Resolved state */}
      {resolved && (
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <svg className="w-3.5 h-3.5 text-state-healthy" viewBox="0 0 16 16" fill="none">
            <path
              d="M3.5 8.5L6.5 11.5L12.5 4.5"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span>
            Resolved{resolvedLabel ? `: ${resolvedLabel}` : ''}
            {resolvedChannel ? ` via ${resolvedChannel}` : ''}
          </span>
        </div>
      )}
    </div>
  )
}
