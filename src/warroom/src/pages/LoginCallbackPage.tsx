import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { exchangeOidcLogin } from '@/api/auth'

export function LoginCallbackPage() {
  const navigate = useNavigate()
  const [error, setError] = useState('')

  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
    const exchangeCode = hash.get('exchange_code') || ''
    const callbackError = hash.get('error') || ''

    if (callbackError) {
      setError(callbackError.replace(/_/g, ' '))
      return
    }

    if (!exchangeCode) {
      setError('Missing OIDC exchange code')
      return
    }

    exchangeOidcLogin(exchangeCode)
      .then(() => {
        navigate('/command', { replace: true })
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Enterprise login failed')
      })
  }, [navigate])

  return (
    <div className="min-h-screen bg-surface-bg flex items-center justify-center px-4">
      <div className="w-full max-w-sm bg-surface-card border border-border-default rounded-xl p-8 shadow-2xl">
        <h1 className="text-lg font-semibold text-text-primary mb-3">Enterprise Sign-In</h1>
        {error ? (
          <p className="text-sm text-red-400">{error}</p>
        ) : (
          <p className="text-sm text-text-secondary">Finishing enterprise login...</p>
        )}
      </div>
    </div>
  )
}
