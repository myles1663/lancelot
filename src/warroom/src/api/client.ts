// ============================================================
// Base API Client
// Typed fetch wrapper with auth, error handling, and retry
// ============================================================

import type { ApiError } from '@/types/api'

const READ_REQUEST_TIMEOUT_MS = 8000
const MUTATION_REQUEST_TIMEOUT_MS = 60000

const API_BASE = '' // Same origin — Vite proxy in dev, FastAPI static in prod

export class ApiClientError extends Error {
  constructor(
    public status: number,
    public body: ApiError,
  ) {
    super(body.error || `API error ${status}`)
    this.name = 'ApiClientError'
  }
}

function timeoutError(timeoutMs: number): ApiClientError {
  return new ApiClientError(408, {
    error: `API request timed out after ${timeoutMs}ms`,
    status: 408,
  })
}

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    return await fetch(input, {
      ...init,
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw timeoutError(timeoutMs)
    }
    throw error
  } finally {
    window.clearTimeout(timeoutId)
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    // Session expired — redirect to login
    if (res.status === 401) {
      window.location.href = '/war-room/login'
      throw new ApiClientError(401, { error: 'Session expired', status: 401 })
    }
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
  const res = await fetchWithTimeout(
    url.toString(),
    { credentials: 'include' },
    READ_REQUEST_TIMEOUT_MS,
  )
  return handleResponse<T>(res)
}

export async function apiPost<T>(path: string, body?: unknown, timeoutMs = MUTATION_REQUEST_TIMEOUT_MS): Promise<T> {
  const res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
    timeoutMs,
  )
  return handleResponse<T>(res)
}

export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    {
      method: 'PUT',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
    MUTATION_REQUEST_TIMEOUT_MS,
  )
  return handleResponse<T>(res)
}

export async function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    {
      method: 'PATCH',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
    MUTATION_REQUEST_TIMEOUT_MS,
  )
  return handleResponse<T>(res)
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    {
      method: 'DELETE',
      credentials: 'include',
    },
    MUTATION_REQUEST_TIMEOUT_MS,
  )
  return handleResponse<T>(res)
}

export async function apiPostForm<T>(
  path: string,
  formData: FormData,
  timeoutMs = MUTATION_REQUEST_TIMEOUT_MS,
): Promise<T> {
  const res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    {
      method: 'POST',
      credentials: 'include',
      body: formData,
    },
    timeoutMs,
  )
  return handleResponse<T>(res)
}
