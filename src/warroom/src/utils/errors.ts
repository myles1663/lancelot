import { ApiClientError } from '@/api'

export function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiClientError) {
    return error.body.detail || error.body.error || fallback
  }
  if (error instanceof Error) {
    return error.message
  }
  return fallback
}
