// ============================================================
// War Room Authentication API
// ============================================================

const API_BASE = ''

export interface LoginResponse {
  expires_in: number
  username: string
  operator_id?: string
  session_id?: string
}

export interface ValidateResponse {
  valid: boolean
  remaining_seconds: number
  username: string
}

export interface AuthConfigResponse {
  provider: 'local' | 'oidc'
  local: {
    enabled: boolean
    password_reset_enabled: boolean
    username_hint?: string
  }
  oidc: {
    enabled: boolean
    configured: boolean
    display_name: string
    login_path: string
  }
}

export async function getAuthConfig(): Promise<AuthConfigResponse> {
  const res = await fetch(`${API_BASE}/auth/config`, {
    credentials: 'include',
  })
  if (!res.ok) {
    throw new Error('Failed to load authentication configuration')
  }
  return res.json()
}

export async function login(
  username: string,
  password: string,
): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(body.error || 'Login failed')
  }
  return res.json()
}

export async function validateSession(): Promise<ValidateResponse> {
  const res = await fetch(`${API_BASE}/auth/validate`, {
    method: 'POST',
    credentials: 'include',
  })
  if (!res.ok) {
    return { valid: false, remaining_seconds: 0, username: '' }
  }
  return res.json()
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE}/auth/change-password`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(body.error || 'Password change failed')
  }
  return res.json()
}

export async function resetPassword(
  username: string,
  resetCode: string,
  newPassword: string,
): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE}/auth/reset-password`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username,
      reset_code: resetCode,
      new_password: newPassword,
    }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(body.error || 'Password reset failed')
  }
  return res.json()
}

export async function exchangeOidcLogin(
  exchangeCode: string,
): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/auth/oidc/exchange`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ exchange_code: exchangeCode }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(body.error || 'OIDC login exchange failed')
  }
  return res.json()
}

export function getOidcLoginUrl(loginPath: string): string {
  return `${API_BASE}${loginPath}`
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  }).catch(() => {})
}
