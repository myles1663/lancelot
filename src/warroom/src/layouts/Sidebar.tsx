import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Archive,
  Bot,
  Calendar,
  Clock,
  Coins,
  Database,
  FileCheck,
  Flag,
  Gauge,
  GitBranch,
  HeartPulse,
  History,
  KeyRound,
  Link2,
  Network,
  Package,
  Plug,
  ReceiptText,
  Route,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  type LucideIcon,
} from 'lucide-react'
import { NavLink, useLocation } from 'react-router-dom'
import logo from '../assets/logo.png'

interface NavItem {
  label: string
  path: string
  icon: LucideIcon
  shortcut?: string
  critical?: boolean
}

interface NavGroup {
  title: string
  summary: string
  items: NavItem[]
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: 'COMMAND',
    summary: 'Run',
    items: [
      { label: 'Command Center', path: '/command', icon: Gauge, shortcut: '1' },
      { label: 'Scheduler', path: '/scheduler', icon: Calendar, shortcut: '9' },
    ],
  },
  {
    title: 'GOVERNANCE',
    summary: 'Review',
    items: [
      { label: 'Governance Dashboard', path: '/governance', icon: ShieldCheck, shortcut: '2' },
      { label: 'Trust Ledger', path: '/trust', icon: FileCheck, shortcut: '4' },
      { label: 'Approval Learning', path: '/apl', icon: Activity, shortcut: '5' },
      { label: 'A2A Protocol', path: '/a2a', icon: Route },
      { label: 'Compliance Export', path: '/compliance', icon: Archive },
    ],
  },
  {
    title: 'MEMORY & SOUL',
    summary: 'Identity',
    items: [
      { label: 'Soul Inspector', path: '/soul', icon: Shield, shortcut: '3' },
      { label: 'Memory', path: '/memory', icon: Database, shortcut: '8' },
      { label: 'Governed Memory Manager', path: '/memory/manage', icon: KeyRound },
      { label: 'Context Efficiency', path: '/memory/context', icon: SlidersHorizontal },
      { label: 'Skills', path: '/skills', icon: Package },
    ],
  },
  {
    title: 'OPERATIONS',
    summary: 'Act',
    items: [
      { label: 'HIVE Agent Mesh', path: '/hive', icon: Bot },
      { label: 'Receipt Explorer', path: '/receipts', icon: ReceiptText, shortcut: '6' },
      { label: 'Tool Fabric', path: '/tools', icon: Plug, shortcut: '7' },
      { label: 'Incident Response', path: '/incidents', icon: AlertTriangle, critical: true },
      { label: 'Fleet Dashboard', path: '/federation/fleet', icon: Network },
      { label: 'Federation Overview', path: '/federation', icon: GitBranch },
      { label: 'Graph Builder', path: '/federation/graph', icon: Link2 },
      { label: 'Federation Audit Trail', path: '/federation/audit', icon: History },
    ],
  },
  {
    title: 'SYSTEM',
    summary: 'Maintain',
    items: [
      { label: 'Health', path: '/health', icon: HeartPulse },
      { label: 'Setup & Recovery', path: '/setup', icon: Flag },
      { label: 'Connectors', path: '/connectors', icon: Link2 },
      { label: 'Cost Tracker', path: '/costs', icon: Coins },
      { label: 'Kill Switches', path: '/flags', icon: SlidersHorizontal },
      { label: 'Time-Travel Debugger', path: '/timetravel', icon: Clock },
    ],
  },
]

const DEFAULT_GROUP_TITLE = 'COMMAND'

interface SidebarProps {
  collapsed: boolean
  onToggle: () => void
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const location = useLocation()
  const activeGroupTitle = useMemo(() => {
    const matchingGroup = NAV_GROUPS.find((group) =>
      group.items.some((item) =>
        location.pathname === item.path || location.pathname.startsWith(`${item.path}/`),
      ),
    )
    return matchingGroup?.title ?? DEFAULT_GROUP_TITLE
  }, [location.pathname])

  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>(
    () => Object.fromEntries(
      NAV_GROUPS.map((g) => [g.title, g.title === activeGroupTitle || g.title === DEFAULT_GROUP_TITLE]),
    ),
  )

  useEffect(() => {
    setExpandedGroups((prev) => ({ ...prev, [activeGroupTitle]: true }))
  }, [activeGroupTitle])

  const toggleGroup = (title: string) => {
    setExpandedGroups((prev) => ({ ...prev, [title]: !prev[title] }))
  }

  const handleNavigate = () => {
    if (typeof window !== 'undefined' && window.innerWidth < 1024) {
      onToggle()
    }
  }

  return (
    <>
      {!collapsed && (
        <button
          type="button"
          onClick={onToggle}
          className="fixed inset-x-0 top-14 bottom-12 z-30 bg-black/45 backdrop-blur-[1px] lg:hidden"
          aria-label="Close navigation"
        />
      )}

      <aside
        className={`fixed top-14 left-0 bottom-12 z-40 w-72 max-w-[calc(100vw-3rem)] bg-[#11141d] border-r border-border-default transition-[transform,width] duration-200 overflow-y-auto shadow-[12px_0_32px_rgba(0,0,0,0.18)] lg:max-w-none ${
          collapsed ? '-translate-x-full lg:w-0' : 'translate-x-0 lg:w-60'
        }`}
      >
        {/* Branding */}
        <div className="px-4 py-3 border-b border-border-default bg-[#151925]">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3 min-w-0">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-border-default bg-surface-card">
                <img
                  src={logo}
                  alt="Lancelot"
                  className="h-8 w-8 object-contain"
                />
              </div>
              <div className="min-w-0">
                <h1 className="text-sm font-semibold text-text-primary tracking-wide leading-tight">LANCELOT</h1>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-text-muted uppercase tracking-widest">OS</span>
                  <span className="text-[10px] text-text-muted font-mono">v{__LANCELOT_VERSION__}</span>
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={onToggle}
              className="p-1.5 rounded-md text-text-muted hover:text-text-primary hover:bg-surface-card-elevated transition-colors"
              aria-label="Collapse sidebar"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M10 4L6 8L10 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>
        </div>

        {/* Navigation Groups */}
        <nav className="px-3 py-4" aria-label="War Room navigation">
          {NAV_GROUPS.map((group) => {
            const expanded = expandedGroups[group.title]
            const active = group.title === activeGroupTitle
            const hasCritical = group.items.some((item) => item.critical)

            return (
              <div key={group.title} className="mb-4 last:mb-2">
                <button
                  type="button"
                  onClick={() => toggleGroup(group.title)}
                  aria-expanded={expanded}
                  className={`mb-1 flex w-full items-center justify-between px-1 py-1 text-left transition-colors ${
                    active
                      ? 'text-text-secondary'
                      : 'text-text-muted hover:text-text-secondary'
                  }`}
                >
                  <span className="truncate text-[10px] font-semibold uppercase tracking-[0.16em]">{group.title}</span>
                  <span className="flex shrink-0 items-center gap-2">
                    {hasCritical && <span className="h-1.5 w-1.5 rounded-full bg-state-error" />}
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 12 12"
                      fill="none"
                      className={`transition-transform ${expanded ? 'rotate-0' : '-rotate-90'}`}
                    >
                      <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </span>
                </button>

                {expanded && (
                  <div className="space-y-0.5">
                    {group.items.map((item) => {
                      const Icon = item.icon

                      return (
                        <NavLink
                          key={item.path}
                          to={item.path}
                          end
                          onClick={handleNavigate}
                          className={({ isActive }) =>
                            `flex min-h-8 items-center justify-between rounded-md px-2.5 py-1.5 text-[13px] transition-colors ${
                              isActive && item.critical
                                ? 'bg-state-error/10 text-state-error shadow-[inset_3px_0_0_theme(colors.state.error)]'
                                : isActive
                                  ? 'bg-accent-primary/12 text-text-primary shadow-[inset_3px_0_0_theme(colors.accent.primary)]'
                                  : item.critical
                                    ? 'text-state-error/90 hover:text-state-error hover:bg-state-error/5'
                                    : 'text-text-secondary hover:text-text-primary hover:bg-surface-card/80'
                            }`
                          }
                        >
                          <span className="flex min-w-0 items-center gap-2.5">
                            <Icon className="h-4 w-4 shrink-0" strokeWidth={1.8} aria-hidden="true" />
                            <span className="truncate">{item.label}</span>
                          </span>
                          {item.shortcut && (
                            <kbd className="ml-2 hidden shrink-0 rounded bg-surface-input/70 px-1 text-[10px] font-mono text-text-muted lg:inline">
                              ^{item.shortcut}
                            </kbd>
                          )}
                        </NavLink>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </nav>
      </aside>
    </>
  )
}
