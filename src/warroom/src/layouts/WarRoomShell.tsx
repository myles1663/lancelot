import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { NotificationTray } from './NotificationTray'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'

export function WarRoomShell() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  useKeyboardShortcuts()

  return (
    <div className="min-h-screen bg-surface-bg text-text-primary">
      <Header
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebar={() => setSidebarCollapsed(false)}
      />

      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(true)}
      />

      {/* Primary content area */}
      <main
        className={`pt-14 pb-12 transition-all duration-200 ${
          sidebarCollapsed ? 'pl-0' : 'pl-60'
        }`}
      >
        <div className="p-6 max-w-[1600px] mx-auto">
          <Outlet />
        </div>
      </main>

      <NotificationTray sidebarCollapsed={sidebarCollapsed} />
    </div>
  )
}
