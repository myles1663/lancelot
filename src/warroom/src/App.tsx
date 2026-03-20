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
  FederationOverview,
  GraphBuilder,
  FederationAudit,
  ComplianceExport,
  TimeTravelDebugger,
  IncidentsDashboard,
  A2AManagement,
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
          <Route path="/a2a" element={<A2AManagement />} />
          <Route path="/scheduler" element={<SchedulerPanel />} />

          {/* SYSTEM */}
          <Route path="/health" element={<HealthDashboard />} />
          <Route path="/setup" element={<SetupRecovery />} />
          <Route path="/connectors" element={<Connectors />} />
          <Route path="/costs" element={<CostTracker />} />
          <Route path="/flags" element={<KillSwitches />} />

          {/* FEDERATION */}
          <Route path="/federation" element={<FederationOverview />} />
          <Route path="/federation/graph" element={<GraphBuilder />} />
          <Route path="/federation/audit" element={<FederationAudit />} />

          {/* COMPLIANCE */}
          <Route path="/compliance" element={<ComplianceExport />} />

          {/* TIME-TRAVEL */}
          <Route path="/timetravel" element={<TimeTravelDebugger />} />

          {/* INCIDENTS */}
          <Route path="/incidents" element={<IncidentsDashboard />} />

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
