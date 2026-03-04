// ============================================================
// War Room Authentication API
// ============================================================

const API_BASE = ''

export interface LoginResponse {
  token: string
  expires_in: number
  username: string
}

export interface ValidateResponse {
  valid: boolean
  remaining_seconds: number
  username: string
}

export async function login(
  username: string,
  password: string,
): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
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
  const token = localStorage.getItem('lancelot_api_token')
  if (!token) {
    return { valid: false, remaining_seconds: 0, username: '' }
  }
  const res = await fetch(`${API_BASE}/auth/validate`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) {
    return { valid: false, remaining_seconds: 0, username: '' }
  }
  return res.json()
}

export async function logout(): Promise<void> {
  const token = localStorage.getItem('lancelot_api_token')
  if (token) {
    await fetch(`${API_BASE}/auth/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    }).catch(() => {})
  }
  localStorage.removeItem('lancelot_api_token')
  localStorage.removeItem('lancelot_session_expires')
}
