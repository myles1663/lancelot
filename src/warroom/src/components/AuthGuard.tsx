import { useEffect, useState, useCallback } from 'react'
import { Outlet, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { validateSession, logout } from '@/api/auth'
import { SessionExpiryModal } from './SessionExpiryModal'
import { getErrorMessage } from '@/utils/errors'
import { emitWarRoomNotification } from '@/utils/notifications'

type AuthState = 'checking' | 'authenticated' | 'unauthenticated'

const CHECK_INTERVAL_MS = 60_000 // Check session every 60s
const WARNING_THRESHOLD_S = 300 // Show warning when <5 min remaining

export function AuthGuard() {
  const navigate = useNavigate()
  const location = useLocation()
  const [authState, setAuthState] = useState<AuthState>('checking')
  const [showExpiryModal, setShowExpiryModal] = useState(false)
  const [remainingSeconds, setRemainingSeconds] = useState(0)
  const [suppressWarningUntil, setSuppressWarningUntil] = useState(0)

  const checkSession = useCallback(async () => {
    try {
      const res = await validateSession()
      if (!res.valid) {
        setAuthState('unauthenticated')
        return
      }
      setRemainingSeconds(res.remaining_seconds)
      setAuthState('authenticated')
      // Only show warning if not suppressed (prevents flash after "Stay Signed In")
      if (res.remaining_seconds < WARNING_THRESHOLD_S && Date.now() > suppressWarningUntil) {
        setShowExpiryModal(true)
      }
    } catch {
      setAuthState('unauthenticated')
    }
  }, [suppressWarningUntil])

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
    // Suppress the warning for 60s so the modal doesn't flash back
    // (the validate call refreshes the session, but the next periodic
    // check might fire before state fully updates)
    setSuppressWarningUntil(Date.now() + 60_000)
    await checkSession()
  }, [checkSession])

  const handleSignOut = useCallback(async () => {
    setShowExpiryModal(false)
    try {
      await logout()
      navigate('/login', { replace: true })
    } catch (error) {
      emitWarRoomNotification(getErrorMessage(error, 'Sign out failed'), 'high')
    }
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
    return (
      <Navigate
        to="/login"
        replace
        state={{ returnTo: `${location.pathname}${location.search}${location.hash}` }}
      />
    )
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
