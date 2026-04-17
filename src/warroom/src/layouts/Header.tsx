import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { VitalsBar } from './VitalsBar'
import { logout } from '@/api/auth'
import { ChangePasswordModal } from '@/components/ChangePasswordModal'

interface HeaderProps {
  sidebarCollapsed: boolean
  onToggleSidebar: () => void
}

export function Header({ sidebarCollapsed, onToggleSidebar }: HeaderProps) {
  const navigate = useNavigate()
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [showPasswordModal, setShowPasswordModal] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // Close menu on outside click
  useEffect(() => {
    if (!userMenuOpen) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [userMenuOpen])

  const handleSignOut = async () => {
    setUserMenuOpen(false)
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <>
      <header className="fixed top-0 left-0 right-0 z-50 h-14 bg-surface-card border-b border-border-default flex items-center px-4 gap-4">
        {/* Sidebar toggle (visible when collapsed) */}
        {sidebarCollapsed && (
          <button
            onClick={onToggleSidebar}
            className="p-1.5 text-text-muted hover:text-text-primary transition-colors"
            aria-label="Open sidebar"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path d="M3 5H15M3 9H15M3 13H15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        )}

        {/* Live Vitals Bar */}
        <VitalsBar />

        {/* Notification badge + User menu */}
        <div className="flex items-center gap-2">
          <button className="relative p-2 text-text-muted hover:text-text-primary transition-colors">
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path
                d="M13.5 6.75C13.5 5.56 13.03 4.42 12.18 3.57C11.33 2.72 10.19 2.25 9 2.25C7.81 2.25 6.67 2.72 5.82 3.57C4.97 4.42 4.5 5.56 4.5 6.75C4.5 12 2.25 13.5 2.25 13.5H15.75C15.75 13.5 13.5 12 13.5 6.75Z"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M10.3 15.75C10.17 15.98 9.98 16.17 9.74 16.3C9.51 16.44 9.26 16.5 9 16.5C8.74 16.5 8.49 16.44 8.26 16.3C8.02 16.17 7.83 15.98 7.7 15.75"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <span className="absolute -top-0.5 -right-0.5 w-4 h-4 bg-state-error text-white text-[9px] font-bold rounded-full flex items-center justify-center">
              0
            </span>
          </button>

          {/* User avatar / menu */}
          <div className="relative" ref={menuRef}>
            <button
              onClick={() => setUserMenuOpen(!userMenuOpen)}
              className="w-8 h-8 rounded-full bg-accent-primary/20 text-accent-primary text-xs font-semibold flex items-center justify-center hover:bg-accent-primary/30 transition-colors"
              aria-label="User menu"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M8 8C9.65 8 11 6.65 11 5C11 3.35 9.65 2 8 2C6.35 2 5 3.35 5 5C5 6.65 6.35 8 8 8Z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M13.5 14C13.5 11.5 11.04 9.5 8 9.5C4.96 9.5 2.5 11.5 2.5 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>

            {userMenuOpen && (
              <div className="absolute right-0 top-full mt-1 w-48 bg-surface-card border border-border-default rounded-lg shadow-xl py-1 z-50">
                <button
                  onClick={() => {
                    setUserMenuOpen(false)
                    setShowPasswordModal(true)
                  }}
                  className="w-full text-left px-4 py-2 text-sm text-text-secondary hover:text-text-primary hover:bg-surface-card-elevated transition-colors flex items-center gap-2"
                >
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                    <path d="M7.58 1.17L8.41 3.41L10.83 3.83L9.17 5.42L9.58 7.83L7.58 6.75L5.58 7.83L5.99 5.42L4.33 3.83L6.75 3.41L7.58 1.17Z" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round"/>
                    <rect x="2" y="9" width="10" height="3.5" rx="1" stroke="currentColor" strokeWidth="1"/>
                    <circle cx="5" cy="10.75" r="0.75" fill="currentColor"/>
                  </svg>
                  Change Password
                </button>
                <div className="border-t border-border-default my-1" />
                <button
                  onClick={handleSignOut}
                  className="w-full text-left px-4 py-2 text-sm text-state-error hover:bg-state-error/5 transition-colors flex items-center gap-2"
                >
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                    <path d="M5.25 12.25H2.92C2.61 12.25 2.32 12.13 2.1 11.9C1.88 11.68 1.75 11.39 1.75 11.08V2.92C1.75 2.61 1.88 2.32 2.1 2.1C2.32 1.88 2.61 1.75 2.92 1.75H5.25" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
                    <path d="M9.33 9.92L12.25 7L9.33 4.08" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
                    <path d="M12.25 7H5.25" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                  Sign Out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {showPasswordModal && (
        <ChangePasswordModal onClose={() => setShowPasswordModal(false)} />
      )}
    </>
  )
}
