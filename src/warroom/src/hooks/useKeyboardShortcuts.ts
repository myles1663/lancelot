import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

const TAB_ROUTES = [
  '/command',         // Ctrl+1
  '/governance',      // Ctrl+2
  '/soul',            // Ctrl+3
  '/trust',           // Ctrl+4
  '/apl',             // Ctrl+5
  '/receipts',        // Ctrl+6
  '/tools',           // Ctrl+7
  '/memory',          // Ctrl+8
  '/scheduler',       // Ctrl+9
]

export function useKeyboardShortcuts() {
  const navigate = useNavigate()

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (!e.ctrlKey && !e.metaKey) return
      const digit = parseInt(e.key, 10)
      if (digit >= 1 && digit <= 9) {
        e.preventDefault()
        const route = TAB_ROUTES[digit - 1]
        if (route) navigate(route)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [navigate])
}
