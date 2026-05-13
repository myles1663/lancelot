import { useEffect, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  getAuthConfig,
  getOidcLoginUrl,
  login,
  resetPassword,
  validateSession,
  type AuthConfigResponse,
} from '@/api/auth'
import { ApiClientError } from '@/api/client'
import logo from '@/assets/logo.png'
import { getErrorMessage } from '@/utils/errors'

function isSafeReturnPath(value: unknown): value is string {
  return (
    typeof value === 'string'
    && value.startsWith('/')
    && !value.startsWith('//')
    && !value.startsWith('/login')
  )
}

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [authConfig, setAuthConfig] = useState<AuthConfigResponse | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [resetCode, setResetCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [resetMode, setResetMode] = useState(false)
  const stateReturnTo = (location.state as { returnTo?: unknown } | null)?.returnTo
  const queryReturnTo = new URLSearchParams(location.search).get('return_to')
  const returnTo = isSafeReturnPath(stateReturnTo)
    ? stateReturnTo
    : isSafeReturnPath(queryReturnTo)
      ? queryReturnTo
      : '/command'

  useEffect(() => {
    document.title = 'Sign In | Lancelot War Room'
    return () => {
      document.title = 'Lancelot War Room'
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    validateSession()
      .then((session) => {
        if (!cancelled && session.valid) {
          navigate(returnTo, { replace: true })
        }
      })
      .catch((error) => {
        if (cancelled) return
        if (error instanceof ApiClientError && error.status === 401) {
          return
        }
        setError(getErrorMessage(error, 'Failed to validate existing session'))
      })
    return () => {
      cancelled = true
    }
  }, [navigate, returnTo])

  useEffect(() => {
    let cancelled = false
    getAuthConfig()
      .then((config) => {
        if (cancelled) return
        setAuthConfig(config)
        if (config.local.username_hint) {
          setUsername(config.local.username_hint)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load authentication settings')
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
      navigate(returnTo, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  const handleReset = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    if (newPassword !== confirmPassword) {
      setError('New passwords do not match')
      return
    }
    setLoading(true)
    try {
      await resetPassword(username, resetCode, newPassword)
      setResetMode(false)
      setPassword('')
      setResetCode('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Password reset failed')
    } finally {
      setLoading(false)
    }
  }

  const renderLocalForm = () => (
    <>
      <form
        onSubmit={resetMode ? handleReset : handleSubmit}
        className="bg-surface-card border border-border-default rounded-xl p-8 shadow-2xl"
      >
        <div className="flex flex-col items-center mb-8">
          <img
            src={logo}
            alt="Lancelot"
            className="w-52 h-52 object-contain mb-4"
          />
          <h1 className="text-lg font-semibold text-text-primary tracking-widest">
            LANCELOT
          </h1>
          <span className="text-xs text-text-muted tracking-wider mt-0.5">
            WAR ROOM
          </span>
        </div>

        {error && (
          <div className="mb-4 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-400">
            {error}
          </div>
        )}

        <div className="mb-4">
          <label
            htmlFor="username"
            className="block text-xs font-medium text-text-secondary mb-1.5"
          >
            Username
          </label>
          <input
            id="username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full px-3 py-2.5 bg-surface-input border border-border-default rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary transition-colors"
            placeholder="Enter username"
            autoComplete="username"
            autoFocus
            required
          />
        </div>

        {!resetMode ? (
          <div className="mb-6">
            <label
              htmlFor="password"
              className="block text-xs font-medium text-text-secondary mb-1.5"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2.5 bg-surface-input border border-border-default rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary transition-colors"
              placeholder="Enter password"
              autoComplete="current-password"
              required
            />
          </div>
        ) : (
          <>
            <div className="mb-4">
              <label
                htmlFor="reset-code"
                className="block text-xs font-medium text-text-secondary mb-1.5"
              >
                Reset Code
              </label>
              <input
                id="reset-code"
                type="password"
                value={resetCode}
                onChange={(e) => setResetCode(e.target.value)}
                className="w-full px-3 py-2.5 bg-surface-input border border-border-default rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary transition-colors"
                placeholder="Enter reset code"
                autoComplete="one-time-code"
                required
              />
            </div>
            <div className="mb-4">
              <label
                htmlFor="new-password"
                className="block text-xs font-medium text-text-secondary mb-1.5"
              >
                New Password
              </label>
              <input
                id="new-password"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full px-3 py-2.5 bg-surface-input border border-border-default rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary transition-colors"
                placeholder="Enter new password"
                autoComplete="new-password"
                required
              />
            </div>
            <div className="mb-6">
              <label
                htmlFor="confirm-password"
                className="block text-xs font-medium text-text-secondary mb-1.5"
              >
                Confirm New Password
              </label>
              <input
                id="confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full px-3 py-2.5 bg-surface-input border border-border-default rounded-lg text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-primary transition-colors"
                placeholder="Confirm new password"
                autoComplete="new-password"
                required
              />
            </div>
          </>
        )}

        <button
          type="submit"
          disabled={
            loading
            || !username
            || (!resetMode && !password)
            || (resetMode && (!resetCode || !newPassword || !confirmPassword))
          }
          className="w-full py-2.5 bg-accent-primary hover:bg-accent-primary/90 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
        >
          {loading ? (resetMode ? 'Resetting password...' : 'Signing in...') : (resetMode ? 'Reset Password' : 'Sign In')}
        </button>

        {authConfig?.local.password_reset_enabled && (
          <button
            type="button"
            onClick={() => {
              setError('')
              setResetMode((current) => !current)
            }}
            className="w-full mt-3 text-xs text-text-secondary hover:text-text-primary transition-colors"
          >
            {resetMode ? 'Back to sign in' : 'Reset password'}
          </button>
        )}
      </form>
    </>
  )

  const renderOidcCard = () => (
    <div className="bg-surface-card border border-border-default rounded-xl p-8 shadow-2xl">
      <div className="flex flex-col items-center mb-8">
        <img
          src={logo}
          alt="Lancelot"
          className="w-52 h-52 object-contain mb-4"
        />
        <h1 className="text-lg font-semibold text-text-primary tracking-widest">
          LANCELOT
        </h1>
        <span className="text-xs text-text-muted tracking-wider mt-0.5">
          WAR ROOM
        </span>
      </div>

      {error && (
        <div className="mb-4 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-400">
          {error}
        </div>
      )}

      <p className="text-sm text-text-secondary mb-6">
        This Lancelot deployment uses enterprise single sign-on.
      </p>

      <a
        href={getOidcLoginUrl(authConfig?.oidc.login_path || '/auth/oidc/login')}
        onClick={() => {
          window.sessionStorage.setItem('warRoomReturnTo', returnTo)
        }}
        className="block w-full py-2.5 bg-accent-primary hover:bg-accent-primary/90 text-white text-sm font-medium rounded-lg transition-colors text-center"
      >
        {authConfig?.oidc.display_name || 'Continue with Enterprise SSO'}
      </a>
    </div>
  )

  return (
    <div className="min-h-screen bg-surface-bg flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {!authConfig ? (
          <div className="bg-surface-card border border-border-default rounded-xl p-8 shadow-2xl text-sm text-text-secondary">
            Loading authentication settings...
          </div>
        ) : authConfig.provider === 'oidc' ? renderOidcCard() : renderLocalForm()}
      </div>
    </div>
  )
}
