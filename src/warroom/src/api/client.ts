// ============================================================
// Base API Client
// Typed fetch wrapper with auth, error handling, and retry
// ============================================================

import type { ApiError } from '@/types/api'

const API_BASE = '' // Same origin — Vite proxy in dev, FastAPI static in prod

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem('lancelot_api_token')
  if (token) {
    return { Authorization: `Bearer ${token}` }
  }
  return {}
}

export class ApiClientError extends Error {
  constructor(
    public status: number,
    public body: ApiError,
  ) {
    super(body.error || `API error ${status}`)
    this.name = 'ApiClientError'
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let body: ApiError
    try {
      body = await res.json()
    } catch {
      body = { error: res.statusText, status: res.status }
    }
    throw new ApiClientError(res.status, body)
  }
  return res.json() as Promise<T>
}

export async function apiGet<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${API_BASE}${path}`, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v) url.searchParams.set(k, v)
    })
  }
  const res = await fetch(url.toString(), {
    headers: { ...getAuthHeaders() },
  })
  return handleResponse<T>(res)
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders(),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  return handleResponse<T>(res)
}

export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders(),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  return handleResponse<T>(res)
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    headers: { ...getAuthHeaders() },
  })
  return handleResponse<T>(res)
}

export async function apiPostForm<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { ...getAuthHeaders() },
    body: formData,
  })
  return handleResponse<T>(res)
}
