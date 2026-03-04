import { Routes, Route, Navigate } from 'react-router-dom'
import { WarRoomShell } from '@/layouts'
import { AuthGuard } from '@/components/AuthGuard'
import {
  CommandCenter,
  GovernanceDashboard,
  SoulInspector,
  TrustLedger,
  AplPanel,
  ReceiptExplorer,
  ToolFabric,
  MemoryPanel,
  SchedulerPanel,
  SetupRecovery,
  CostTracker,
  KillSwitches,
  Connectors,
  BusinessDashboard,
  HealthDashboard,
  SkillsPanel,
  HiveAgentMesh,
  LoginPage,
} from '@/pages'

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AuthGuard />}>
        <Route element={<WarRoomShell />}>
          {/* COMMAND */}
          <Route path="/command" element={<CommandCenter />} />

          {/* GOVERNANCE */}
          <Route path="/governance" element={<GovernanceDashboard />} />
          <Route path="/soul" element={<SoulInspector />} />
          <Route path="/trust" element={<TrustLedger />} />
          <Route path="/apl" element={<AplPanel />} />

          {/* OPERATIONS */}
          <Route path="/hive" element={<HiveAgentMesh />} />
          <Route path="/receipts" element={<ReceiptExplorer />} />
          <Route path="/tools" element={<ToolFabric />} />
          <Route path="/memory" element={<MemoryPanel />} />
          <Route path="/skills" element={<SkillsPanel />} />
          <Route path="/scheduler" element={<SchedulerPanel />} />

          {/* SYSTEM */}
          <Route path="/health" element={<HealthDashboard />} />
          <Route path="/setup" element={<SetupRecovery />} />
          <Route path="/connectors" element={<Connectors />} />
          <Route path="/costs" element={<CostTracker />} />
          <Route path="/flags" element={<KillSwitches />} />

          {/* BUSINESS */}
          <Route path="/business" element={<BusinessDashboard />} />

          {/* Default redirect */}
          <Route path="/" element={<Navigate to="/command" replace />} />
          <Route path="*" element={<Navigate to="/command" replace />} />
        </Route>
      </Route>
    </Routes>
  )
}

export default App
