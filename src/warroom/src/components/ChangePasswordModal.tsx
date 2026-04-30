import { useState } from 'react'
import { changePassword } from '@/api/auth'

interface ChangePasswordModalProps {
  onClose: () => void
}

export function ChangePasswordModal({ onClose }: ChangePasswordModalProps) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (newPassword.length < 6) {
      setError('New password must be at least 6 characters')
      return
    }

    if (newPassword !== confirmPassword) {
      setError('New passwords do not match')
      return
    }

    if (currentPassword === newPassword) {
      setError('New password must be different from current password')
      return
    }

    setLoading(true)
    try {
      await changePassword(currentPassword, newPassword)
      setSuccess(true)
      setTimeout(onClose, 1500)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Password change failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60">
      <div className="bg-surface-card border border-border-default rounded-xl p-6 max-w-sm w-full mx-4 shadow-2xl">
        <h2 className="text-base font-semibold text-text-primary mb-4">
          Change Password
        </h2>

        {success ? (
          <div className="py-4 text-center">
            <div className="text-state-healthy font-medium mb-1">Password changed successfully</div>
            <div className="text-sm text-text-muted">Closing...</div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="block text-xs text-text-muted mb-1">Current Password</label>
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="w-full px-3 py-2 text-sm bg-surface-input border border-border-default rounded-lg text-text-primary focus:outline-none focus:border-accent-primary"
                autoFocus
                required
              />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">New Password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full px-3 py-2 text-sm bg-surface-input border border-border-default rounded-lg text-text-primary focus:outline-none focus:border-accent-primary"
                required
                minLength={6}
              />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">Confirm New Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full px-3 py-2 text-sm bg-surface-input border border-border-default rounded-lg text-text-primary focus:outline-none focus:border-accent-primary"
                required
                minLength={6}
              />
            </div>

            {error && (
              <div className="text-sm text-state-error bg-state-error/10 border border-state-error/20 rounded-lg px-3 py-2">
                {error}
              </div>
            )}

            <div className="flex gap-3 justify-end pt-2">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary border border-border-default rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={loading}
                className="px-4 py-2 text-sm text-white bg-accent-primary hover:bg-accent-primary/90 rounded-lg transition-colors disabled:opacity-50"
              >
                {loading ? 'Changing...' : 'Change Password'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
