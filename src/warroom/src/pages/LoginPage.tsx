import { useState, useEffect, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { login } from '@/api/auth'
import logo from '@/assets/logo.jpeg'

export function LoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    document.title = 'Sign In | Lancelot War Room'
    return () => {
      document.title = 'Lancelot War Room'
    }
  }, [])

  // If already authenticated, redirect
  useEffect(() => {
    const token = localStorage.getItem('lancelot_api_token')
    const expires = localStorage.getItem('lancelot_session_expires')
    if (token && expires && Date.now() < Number(expires)) {
      navigate('/command', { replace: true })
    }
  }, [navigate])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await login(username, password)
      localStorage.setItem('lancelot_api_token', res.token)
      localStorage.setItem(
        'lancelot_session_expires',
        String(Date.now() + res.expires_in * 1000),
      )
      navigate('/command', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-surface-bg flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <form
          onSubmit={handleSubmit}
          className="bg-surface-card border border-border-default rounded-xl p-8 shadow-2xl"
        >
          {/* Logo & Title */}
          <div className="flex flex-col items-center mb-8">
            <img
              src={logo}
              alt="Lancelot"
              className="w-20 h-20 rounded-xl object-cover mb-4 shadow-lg"
            />
            <h1 className="text-lg font-semibold text-text-primary tracking-widest">
              LANCELOT
            </h1>
            <span className="text-xs text-text-muted tracking-wider mt-0.5">
              WAR ROOM
            </span>
          </div>

          {/* Error */}
          {error && (
            <div className="mb-4 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-400">
              {error}
            </div>
          )}

          {/* Username */}
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

          {/* Password */}
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

          {/* Submit */}
          <button
            type="submit"
            disabled={loading || !username || !password}
            className="w-full py-2.5 bg-accent-primary hover:bg-accent-primary/90 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  )
}
