import { Routes, Route, Navigate } from 'react-router-dom'
import { WarRoomShell } from '@/layouts'
import { CommandCenter, PlaceholderPage } from '@/pages'

function App() {
  return (
    <Routes>
      <Route element={<WarRoomShell />}>
        {/* COMMAND */}
        <Route path="/command" element={<CommandCenter />} />

        {/* GOVERNANCE */}
        <Route path="/governance" element={<PlaceholderPage title="Governance Dashboard" />} />
        <Route path="/soul" element={<PlaceholderPage title="Soul Inspector" />} />
        <Route path="/trust" element={<PlaceholderPage title="Trust Ledger" />} />
        <Route path="/apl" element={<PlaceholderPage title="Approval Learning" />} />

        {/* OPERATIONS */}
        <Route path="/receipts" element={<PlaceholderPage title="Receipt Explorer" />} />
        <Route path="/tools" element={<PlaceholderPage title="Tool Fabric" />} />
        <Route path="/memory" element={<PlaceholderPage title="Memory" />} />
        <Route path="/scheduler" element={<PlaceholderPage title="Scheduler" />} />

        {/* SYSTEM */}
        <Route path="/setup" element={<PlaceholderPage title="Setup & Recovery" />} />
        <Route path="/costs" element={<PlaceholderPage title="Cost Tracker" />} />
        <Route path="/flags" element={<PlaceholderPage title="Kill Switches" />} />

        {/* BUSINESS */}
        <Route path="/business" element={<PlaceholderPage title="Business Dashboard" />} />

        {/* Default redirect */}
        <Route path="/" element={<Navigate to="/command" replace />} />
        <Route path="*" element={<Navigate to="/command" replace />} />
      </Route>
    </Routes>
  )
}

export default App
