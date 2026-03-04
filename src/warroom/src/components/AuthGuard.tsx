import { useEffect, useState, useCallback } from 'react'
import { Outlet, Navigate, useNavigate } from 'react-router-dom'
import { validateSession, logout } from '@/api/auth'
import { SessionExpiryModal } from './SessionExpiryModal'

type AuthState = 'checking' | 'authenticated' | 'unauthenticated'

const CHECK_INTERVAL_MS = 60_000 // Check session every 60s
const WARNING_THRESHOLD_S = 300 // Show warning when <5 min remaining

export function AuthGuard() {
  const navigate = useNavigate()
  const [authState, setAuthState] = useState<AuthState>('checking')
  const [showExpiryModal, setShowExpiryModal] = useState(false)
  const [remainingSeconds, setRemainingSeconds] = useState(0)

  const checkSession = useCallback(async () => {
    const token = localStorage.getItem('lancelot_api_token')
    if (!token) {
      setAuthState('unauthenticated')
      return
    }
    try {
      const res = await validateSession()
      if (!res.valid) {
        localStorage.removeItem('lancelot_api_token')
        localStorage.removeItem('lancelot_session_expires')
        setAuthState('unauthenticated')
        return
      }
      setRemainingSeconds(res.remaining_seconds)
      setAuthState('authenticated')
      if (res.remaining_seconds < WARNING_THRESHOLD_S) {
        setShowExpiryModal(true)
      }
    } catch {
      setAuthState('unauthenticated')
    }
  }, [])

  // Initial check
  useEffect(() => {
    checkSession()
  }, [checkSession])

  // Periodic check
  useEffect(() => {
    if (authState !== 'authenticated') return
    const id = setInterval(checkSession, CHECK_INTERVAL_MS)
    return () => clearInterval(id)
  }, [authState, checkSession])

  const handleStaySignedIn = useCallback(async () => {
    setShowExpiryModal(false)
    await checkSession()
  }, [checkSession])

  const handleSignOut = useCallback(async () => {
    setShowExpiryModal(false)
    await logout()
    navigate('/login', { replace: true })
  }, [navigate])

  if (authState === 'checking') {
    return (
      <div className="min-h-screen bg-surface-bg flex items-center justify-center">
        <span className="text-text-muted text-sm animate-pulse">
          Verifying session...
        </span>
      </div>
    )
  }

  if (authState === 'unauthenticated') {
    return <Navigate to="/login" replace />
  }

  return (
    <>
      <Outlet />
      {showExpiryModal && (
        <SessionExpiryModal
          remainingSeconds={remainingSeconds}
          onStay={handleStaySignedIn}
          onSignOut={handleSignOut}
        />
      )}
    </>
  )
}
