import { VitalsBar } from './VitalsBar'

interface HeaderProps {
  sidebarCollapsed: boolean
  onToggleSidebar: () => void
}

export function Header({ sidebarCollapsed, onToggleSidebar }: HeaderProps) {
  return (
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

      {/* Notification badge placeholder — WR-13 will replace this */}
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
      </div>
    </header>
  )
}
