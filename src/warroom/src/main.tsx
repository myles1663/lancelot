// Lancelot — A Governed Autonomous System
// Copyright (c) 2026 Myles Russell Hamilton
// Licensed under BUSL-1.1. See LICENSE for details.
// Patent Pending: US Provisional Application #63/982,183

import { Component, StrictMode, type ErrorInfo, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './styles/index.css'

function reportWarRoomClientError(kind: string, error: unknown, extra: Record<string, unknown> = {}) {
  const err = error instanceof Error ? error : new Error(String(error))
  const payload = {
    kind,
    message: err.message,
    stack: err.stack ?? '',
    href: window.location.href,
    user_agent: window.navigator.userAgent,
    ...extra,
  }

  fetch('/api/warroom/client-error', {
    method: 'POST',
    credentials: 'include',
    keepalive: true,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => undefined)
}

window.addEventListener('error', (event) => {
  reportWarRoomClientError('window.error', event.error ?? event.message, {
    source: event.filename,
    line: event.lineno,
    column: event.colno,
  })
})

window.addEventListener('unhandledrejection', (event) => {
  reportWarRoomClientError('window.unhandledrejection', event.reason)
})

class WarRoomErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('War Room render failure', error, errorInfo)
    reportWarRoomClientError('react.render', error, {
      component_stack: errorInfo.componentStack,
    })
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-surface-bg p-6 text-text-primary">
          <div className="mx-auto mt-16 max-w-2xl rounded-lg border border-state-error/40 bg-surface-card p-5">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-state-error">War Room Render Error</p>
            <h1 className="mt-2 text-lg font-semibold">The interface could not finish loading.</h1>
            <p className="mt-2 text-sm text-text-secondary">
              Reload the page. If this repeats, capture this message before continuing the review.
            </p>
            <pre className="mt-4 max-h-60 overflow-auto rounded-md border border-border-default bg-surface-input p-3 text-xs text-text-muted">
              {this.state.error.message}
            </pre>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <WarRoomErrorBoundary>
      <BrowserRouter basename="/war-room">
        <App />
      </BrowserRouter>
    </WarRoomErrorBoundary>
  </StrictMode>,
)
