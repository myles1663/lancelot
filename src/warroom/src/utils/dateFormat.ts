// ============================================================
// Shared Date Formatting Utilities
// Replaces inline new Date() calls across all War Room pages
// ============================================================

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return 'Never'
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    })
  } catch {
    return iso
  }
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return 'Never'
  try {
    const now = Date.now()
    const then = new Date(iso).getTime()
    const diffSec = Math.floor((now - then) / 1000)
    if (diffSec < 0) return 'just now'
    if (diffSec < 60) return 'just now'
    if (diffSec < 3600) {
      const m = Math.floor(diffSec / 60)
      return `${m} min ago`
    }
    if (diffSec < 86400) {
      const h = Math.floor(diffSec / 3600)
      return `${h} hour${h !== 1 ? 's' : ''} ago`
    }
    const d = Math.floor(diffSec / 86400)
    return `${d} day${d !== 1 ? 's' : ''} ago`
  } catch {
    return iso
  }
}

export function formatUptime(seconds: number): string {
  if (!seconds || seconds < 0) return '0m'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

export function formatTimeOnly(iso: string | null | undefined): string {
  if (!iso) return '--'
  try {
    return new Date(iso).toLocaleTimeString('en-US', { hour12: false })
  } catch {
    return iso
  }
}

export function formatDateOnly(iso: string | null | undefined): string {
  if (!iso) return '--'
  try {
    return new Date(iso).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  } catch {
    return iso
  }
}
