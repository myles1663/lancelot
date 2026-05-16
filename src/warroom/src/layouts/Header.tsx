import { useState, useRef, useEffect, useMemo } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Bell, KeyRound, LogOut, Menu, User } from 'lucide-react'
import { VitalsBar } from './VitalsBar'
import { logout } from '@/api/auth'
import { ChangePasswordModal } from '@/components/ChangePasswordModal'
import { getErrorMessage } from '@/utils/errors'
import { emitWarRoomNotification } from '@/utils/notifications'

const HEADER_ROUTES = [
  { path: '/command', label: 'Command Center', section: 'Command' },
  { path: '/scheduler', label: 'Scheduler', section: 'Command' },
  { path: '/governance', label: 'Governance Dashboard', section: 'Governance' },
  { path: '/trust', label: 'Trust Ledger', section: 'Governance' },
  { path: '/apl', label: 'Approval Learning', section: 'Governance' },
  { path: '/a2a', label: 'A2A Protocol', section: 'Governance' },
  { path: '/compliance', label: 'Compliance Export', section: 'Governance' },
  { path: '/soul', label: 'Soul Inspector', section: 'Memory & Soul' },
  { path: '/memory/manage', label: 'Governed Memory Manager', section: 'Memory & Soul' },
  { path: '/memory/context', label: 'Context Efficiency', section: 'Memory & Soul' },
  { path: '/memory', label: 'Memory', section: 'Memory & Soul' },
  { path: '/skills', label: 'Skills', section: 'Memory & Soul' },
  { path: '/hive', label: 'HIVE Agent Mesh', section: 'Operations' },
  { path: '/receipts', label: 'Receipt Explorer', section: 'Operations' },
  { path: '/tools', label: 'Tool Fabric', section: 'Operations' },
  { path: '/incidents', label: 'Incident Response', section: 'Operations' },
  { path: '/federation/fleet', label: 'Fleet Dashboard', section: 'Operations' },
  { path: '/federation/graph', label: 'Graph Builder', section: 'Operations' },
  { path: '/federation/audit', label: 'Federation Audit Trail', section: 'Operations' },
  { path: '/federation', label: 'Federation Overview', section: 'Operations' },
  { path: '/health', label: 'Health', section: 'System' },
  { path: '/setup', label: 'Setup & Recovery', section: 'System' },
  { path: '/connectors', label: 'Connectors', section: 'System' },
  { path: '/costs', label: 'Cost Tracker', section: 'System' },
  { path: '/flags', label: 'Kill Switches', section: 'System' },
  { path: '/timetravel', label: 'Time-Travel Debugger', section: 'System' },
]

interface HeaderProps {
  sidebarCollapsed: boolean
  onToggleSidebar: () => void
  notificationCount: number
}

export function Header({ sidebarCollapsed, onToggleSidebar, notificationCount }: HeaderProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [showPasswordModal, setShowPasswordModal] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const pageContext = useMemo(() => {
    return HEADER_ROUTES.find((route) =>
      location.pathname === route.path || location.pathname.startsWith(`${route.path}/`),
    ) ?? { label: 'War Room', section: 'Lancelot OS' }
  }, [location.pathname])

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
    try {
      await logout()
      navigate('/login', { replace: true })
    } catch (error) {
      emitWarRoomNotification(getErrorMessage(error, 'Sign out failed'), 'high')
    }
  }

  return (
    <>
      <header className="fixed top-0 left-0 right-0 z-50 h-14 bg-surface-card border-b border-border-default flex items-center px-4 gap-4">
        {/* Sidebar toggle (visible when collapsed) */}
        {sidebarCollapsed && (
          <button
            onClick={onToggleSidebar}
            className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-surface-card-elevated hover:text-text-primary"
            aria-label="Open sidebar"
          >
            <Menu className="h-[18px] w-[18px]" strokeWidth={1.8} aria-hidden="true" />
          </button>
        )}

        <div className="hidden min-w-[150px] max-w-[240px] shrink-0 border-r border-border-default pr-4 md:block">
          <p className="truncate text-[10px] font-semibold uppercase tracking-[0.16em] text-text-muted">
            {pageContext.section}
          </p>
          <h1 className="truncate text-sm font-semibold leading-tight text-text-primary">
            {pageContext.label}
          </h1>
        </div>

        <div className="hidden flex-1 justify-center xl:flex">
          <div className="flex items-center gap-2 rounded-md border border-border-default bg-surface-bg/40 px-3 py-1.5">
            <span className="text-xs font-semibold tracking-[0.2em] text-text-primary">LANCELOT</span>
            <span className="h-3 w-px bg-border-active" />
            <span className="text-xs font-semibold tracking-[0.2em] text-accent-primary">WAR ROOM</span>
          </div>
        </div>

        {/* Live Vitals Bar */}
        <div className="flex min-w-0 flex-1 justify-end xl:max-w-[420px] 2xl:max-w-[640px]">
          <VitalsBar />
        </div>

        {/* Notification badge + User menu */}
        <div className="flex items-center gap-2">
          <button className="relative rounded-md p-2 text-text-muted transition-colors hover:bg-surface-card-elevated hover:text-text-primary" aria-label="Notifications">
            <Bell className="h-[18px] w-[18px]" strokeWidth={1.8} aria-hidden="true" />
            {notificationCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-state-error px-1 text-[9px] font-bold text-white">
                {notificationCount > 9 ? '9+' : notificationCount}
              </span>
            )}
          </button>

          {/* User avatar / menu */}
          <div className="relative" ref={menuRef}>
            <button
              onClick={() => setUserMenuOpen(!userMenuOpen)}
              className="w-8 h-8 rounded-full bg-accent-primary/20 text-accent-primary text-xs font-semibold flex items-center justify-center hover:bg-accent-primary/30 transition-colors"
              aria-label="User menu"
            >
              <User className="h-4 w-4" strokeWidth={1.8} aria-hidden="true" />
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
                  <KeyRound className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden="true" />
                  Change Password
                </button>
                <div className="border-t border-border-default my-1" />
                <button
                  onClick={handleSignOut}
                  className="w-full text-left px-4 py-2 text-sm text-state-error hover:bg-state-error/5 transition-colors flex items-center gap-2"
                >
                  <LogOut className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden="true" />
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
