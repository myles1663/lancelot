import { useState } from 'react'
import { NavLink } from 'react-router-dom'

interface NavItem {
  label: string
  path: string
  shortcut?: string
}

interface NavGroup {
  title: string
  items: NavItem[]
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: 'COMMAND',
    items: [
      { label: 'Command Center', path: '/command', shortcut: '1' },
    ],
  },
  {
    title: 'GOVERNANCE',
    items: [
      { label: 'Governance Dashboard', path: '/governance', shortcut: '2' },
      { label: 'Soul Inspector', path: '/soul', shortcut: '3' },
      { label: 'Trust Ledger', path: '/trust', shortcut: '4' },
      { label: 'Approval Learning', path: '/apl', shortcut: '5' },
    ],
  },
  {
    title: 'OPERATIONS',
    items: [
      { label: 'Receipt Explorer', path: '/receipts', shortcut: '6' },
      { label: 'Tool Fabric', path: '/tools', shortcut: '7' },
      { label: 'Memory', path: '/memory', shortcut: '8' },
      { label: 'Scheduler', path: '/scheduler', shortcut: '9' },
    ],
  },
  {
    title: 'SYSTEM',
    items: [
      { label: 'Setup & Recovery', path: '/setup' },
      { label: 'Connectors', path: '/connectors' },
      { label: 'Cost Tracker', path: '/costs' },
      { label: 'Kill Switches', path: '/flags' },
    ],
  },
  {
    title: 'BUSINESS',
    items: [
      { label: 'Business Dashboard', path: '/business' },
    ],
  },
]

interface SidebarProps {
  collapsed: boolean
  onToggle: () => void
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>(
    () => Object.fromEntries(NAV_GROUPS.map((g) => [g.title, true])),
  )

  const toggleGroup = (title: string) => {
    setExpandedGroups((prev) => ({ ...prev, [title]: !prev[title] }))
  }

  return (
    <aside
      className={`fixed top-14 left-0 bottom-12 z-40 bg-surface-card border-r border-border-default transition-all duration-200 overflow-y-auto ${
        collapsed ? 'w-0 -translate-x-full' : 'w-60'
      }`}
    >
      {/* Branding */}
      <div className="px-4 py-4 border-b border-border-default">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-sm font-semibold text-text-primary tracking-wide">LANCELOT OS</h1>
            <span className="text-[10px] text-text-muted font-mono">v7.0</span>
          </div>
          <button
            onClick={onToggle}
            className="p-1 text-text-muted hover:text-text-primary transition-colors"
            aria-label="Collapse sidebar"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M10 4L6 8L10 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
      </div>

      {/* Navigation Groups */}
      <nav className="py-2">
        {NAV_GROUPS.map((group) => (
          <div key={group.title} className="mb-1">
            <button
              onClick={() => toggleGroup(group.title)}
              className="w-full flex items-center justify-between px-4 py-1.5 text-[10px] font-semibold text-text-muted tracking-widest uppercase hover:text-text-secondary transition-colors"
            >
              {group.title}
              <svg
                width="12"
                height="12"
                viewBox="0 0 12 12"
                fill="none"
                className={`transition-transform ${expandedGroups[group.title] ? 'rotate-0' : '-rotate-90'}`}
              >
                <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>

            {expandedGroups[group.title] && (
              <div className="mt-0.5">
                {group.items.map((item) => (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    className={({ isActive }) =>
                      `flex items-center justify-between px-4 py-2 text-sm transition-colors ${
                        isActive
                          ? 'text-accent-primary bg-accent-primary/5 border-l-2 border-accent-primary'
                          : 'text-text-secondary hover:text-text-primary hover:bg-surface-card-elevated border-l-2 border-transparent'
                      }`
                    }
                  >
                    <span>{item.label}</span>
                    {item.shortcut && (
                      <kbd className="text-[10px] text-text-muted font-mono bg-surface-input px-1 rounded">
                        ^{item.shortcut}
                      </kbd>
                    )}
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        ))}
      </nav>
    </aside>
  )
}
