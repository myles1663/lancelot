import { useState } from 'react'

interface InterventionDialogProps {
  open: boolean
  type: 'pause' | 'kill' | 'modify'
  agentId: string
  onConfirm: (reason: string, feedback?: string) => void
  onCancel: () => void
}

const TYPE_CONFIG = {
  pause: {
    title: 'Pause Agent',
    description: 'This will pause the agent. It can be resumed later.',
    confirmLabel: 'Pause',
    confirmClass: 'bg-yellow-600 hover:bg-yellow-500',
  },
  kill: {
    title: 'Kill Agent',
    description: 'This will permanently stop the agent. This action cannot be undone.',
    confirmLabel: 'Kill',
    confirmClass: 'bg-red-600 hover:bg-red-500',
  },
  modify: {
    title: 'Modify Agent',
    description: 'This will kill the current agent and replan with your feedback.',
    confirmLabel: 'Modify & Replan',
    confirmClass: 'bg-blue-600 hover:bg-blue-500',
  },
}

export function InterventionDialog({
  open,
  type,
  agentId,
  onConfirm,
  onCancel,
}: InterventionDialogProps) {
  const [reason, setReason] = useState('')
  const [feedback, setFeedback] = useState('')

  if (!open) return null

  const config = TYPE_CONFIG[type]

  const handleConfirm = () => {
    if (!reason.trim()) return
    onConfirm(reason, feedback || undefined)
    setReason('')
    setFeedback('')
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-surface-card border border-border-default rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="px-6 py-4 border-b border-border-default">
          <h3 className="text-lg font-semibold text-text-primary">{config.title}</h3>
          <p className="text-sm text-text-muted mt-1">
            Agent: <code className="text-xs bg-surface-input px-1 rounded">{agentId}</code>
          </p>
        </div>

        <div className="px-6 py-4 space-y-4">
          <p className="text-sm text-text-secondary">{config.description}</p>

          <div>
            <label className="block text-sm font-medium text-text-primary mb-1">
              Reason <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why are you performing this action?"
              className="w-full px-3 py-2 bg-surface-input border border-border-default rounded text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary"
              autoFocus
            />
          </div>

          {type === 'modify' && (
            <div>
              <label className="block text-sm font-medium text-text-primary mb-1">
                Feedback (optional)
              </label>
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="What should the new plan consider?"
                rows={3}
                className="w-full px-3 py-2 bg-surface-input border border-border-default rounded text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary resize-none"
              />
            </div>
          )}
        </div>

        <div className="px-6 py-3 border-t border-border-default flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={!reason.trim()}
            className={`px-4 py-2 text-sm text-white rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${config.confirmClass}`}
          >
            {config.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
