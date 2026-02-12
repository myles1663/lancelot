import { Routes, Route, Navigate } from 'react-router-dom'
import { WarRoomShell } from '@/layouts'
import {
  CommandCenter,
  GovernanceDashboard,
  SoulInspector,
  TrustLedger,
  AplPanel,
  ReceiptExplorer,
  PlaceholderPage,
} from '@/pages'

function App() {
  return (
    <Routes>
      <Route element={<WarRoomShell />}>
        {/* COMMAND */}
        <Route path="/command" element={<CommandCenter />} />

        {/* GOVERNANCE */}
        <Route path="/governance" element={<GovernanceDashboard />} />
        <Route path="/soul" element={<SoulInspector />} />
        <Route path="/trust" element={<TrustLedger />} />
        <Route path="/apl" element={<AplPanel />} />

        {/* OPERATIONS */}
        <Route path="/receipts" element={<ReceiptExplorer />} />
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
