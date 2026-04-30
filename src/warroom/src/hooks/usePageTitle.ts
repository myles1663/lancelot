import { useEffect } from 'react'

export function usePageTitle(title: string) {
  useEffect(() => {
    const prev = document.title
    document.title = `${title} | Lancelot War Room`
    return () => {
      document.title = prev
    }
  }, [title])
}
