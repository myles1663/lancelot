import { useCallback, useEffect, useRef } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  description: string
  confirmLabel?: string
  cancelLabel?: string
  variant?: 'default' | 'destructive'
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'default',
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    },
    [onCancel],
  )

  if (!open) return null

  const confirmStyles =
    variant === 'destructive'
      ? 'bg-state-error hover:bg-state-error/80 text-white'
      : 'bg-accent-primary hover:bg-accent-primary/80 text-white'

  return (
    <dialog
      ref={dialogRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-transparent backdrop:bg-black/60"
      onKeyDown={handleKeyDown}
    >
      <div className="bg-surface-card-elevated border border-border-default rounded-lg p-6 max-w-md w-full shadow-xl">
        <h3 className="text-lg font-semibold text-text-primary">{title}</h3>
        <p className="text-sm text-text-secondary mt-2">{description}</p>
        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm text-text-secondary bg-surface-input border border-border-default rounded-md hover:bg-surface-card transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-2 text-sm font-medium rounded-md transition-colors ${confirmStyles}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  )
}
