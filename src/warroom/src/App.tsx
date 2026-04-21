import { Suspense, lazy, type ComponentType } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { WarRoomShell } from '@/layouts'
import { AuthGuard } from '@/components/AuthGuard'
import { PageLoader } from '@/components/PageLoader'

const CommandCenterPage = lazy(() => import('@/pages/CommandCenter').then(module => ({ default: module.CommandCenter })))
const GovernanceDashboardPage = lazy(() => import('@/pages/GovernanceDashboard').then(module => ({ default: module.GovernanceDashboard })))
const SoulInspectorPage = lazy(() => import('@/pages/SoulInspector').then(module => ({ default: module.SoulInspector })))
const TrustLedgerPage = lazy(() => import('@/pages/TrustLedger').then(module => ({ default: module.TrustLedger })))
const AplPanelPage = lazy(() => import('@/pages/AplPanel').then(module => ({ default: module.AplPanel })))
const ReceiptExplorerPage = lazy(() => import('@/pages/ReceiptExplorer').then(module => ({ default: module.ReceiptExplorer })))
const ToolFabricPage = lazy(() => import('@/pages/ToolFabric').then(module => ({ default: module.ToolFabric })))
const MemoryPanelPage = lazy(() => import('@/pages/MemoryPanel').then(module => ({ default: module.MemoryPanel })))
const MemoryManagerPageRoute = lazy(() => import('@/pages/MemoryManagerPage').then(module => ({ default: module.MemoryManagerPage })))
const SchedulerPanelPage = lazy(() => import('@/pages/SchedulerPanel').then(module => ({ default: module.SchedulerPanel })))
const SetupRecoveryPage = lazy(() => import('@/pages/SetupRecovery').then(module => ({ default: module.SetupRecovery })))
const CostTrackerPage = lazy(() => import('@/pages/CostTracker').then(module => ({ default: module.CostTracker })))
const KillSwitchesPage = lazy(() => import('@/pages/KillSwitches').then(module => ({ default: module.KillSwitches })))
const ConnectorsPage = lazy(() => import('@/pages/Connectors').then(module => ({ default: module.Connectors })))
const BusinessDashboardPage = lazy(() => import('@/pages/BusinessDashboard').then(module => ({ default: module.BusinessDashboard })))
const HealthDashboardPage = lazy(() => import('@/pages/HealthDashboard').then(module => ({ default: module.HealthDashboard })))
const SkillsPanelPage = lazy(() => import('@/pages/SkillsPanel').then(module => ({ default: module.SkillsPanel })))
const HiveAgentMeshPage = lazy(() => import('@/pages/HiveAgentMesh').then(module => ({ default: module.HiveAgentMesh })))
const LoginPageRoute = lazy(() => import('@/pages/LoginPage').then(module => ({ default: module.LoginPage })))
const LoginCallbackPageRoute = lazy(() => import('@/pages/LoginCallbackPage').then(module => ({ default: module.LoginCallbackPage })))
const FederationOverviewPage = lazy(() => import('@/pages/FederationOverview').then(module => ({ default: module.FederationOverview })))
const GraphBuilderPage = lazy(() => import('@/pages/GraphBuilder').then(module => ({ default: module.GraphBuilder })))
const FederationAuditPage = lazy(() => import('@/pages/FederationAudit').then(module => ({ default: module.FederationAudit })))
const ComplianceExportPage = lazy(() => import('@/pages/ComplianceExport').then(module => ({ default: module.ComplianceExport })))
const TimeTravelDebuggerPage = lazy(() => import('@/pages/TimeTravelDebugger').then(module => ({ default: module.TimeTravelDebugger })))
const IncidentsDashboardPage = lazy(() => import('@/pages/IncidentsDashboard'))
const A2AManagementPage = lazy(() => import('@/pages/A2AManagement').then(module => ({ default: module.A2AManagement })))

function routeElement(Component: ComponentType) {
  return (
    <Suspense fallback={<PageLoader />}>
      <Component />
    </Suspense>
  )
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={routeElement(LoginPageRoute)} />
      <Route path="/login/callback" element={routeElement(LoginCallbackPageRoute)} />
      <Route element={<AuthGuard />}>
        <Route element={<WarRoomShell />}>
          {/* COMMAND */}
          <Route path="/command" element={routeElement(CommandCenterPage)} />

          {/* GOVERNANCE */}
          <Route path="/governance" element={routeElement(GovernanceDashboardPage)} />
          <Route path="/soul" element={routeElement(SoulInspectorPage)} />
          <Route path="/trust" element={routeElement(TrustLedgerPage)} />
          <Route path="/apl" element={routeElement(AplPanelPage)} />

          {/* OPERATIONS */}
          <Route path="/hive" element={routeElement(HiveAgentMeshPage)} />
          <Route path="/receipts" element={routeElement(ReceiptExplorerPage)} />
          <Route path="/tools" element={routeElement(ToolFabricPage)} />
          <Route path="/memory" element={routeElement(MemoryPanelPage)} />
          <Route path="/memory/manage" element={routeElement(MemoryManagerPageRoute)} />
          <Route path="/skills" element={routeElement(SkillsPanelPage)} />
          <Route path="/a2a" element={routeElement(A2AManagementPage)} />
          <Route path="/scheduler" element={routeElement(SchedulerPanelPage)} />

          {/* SYSTEM */}
          <Route path="/health" element={routeElement(HealthDashboardPage)} />
          <Route path="/setup" element={routeElement(SetupRecoveryPage)} />
          <Route path="/connectors" element={routeElement(ConnectorsPage)} />
          <Route path="/costs" element={routeElement(CostTrackerPage)} />
          <Route path="/flags" element={routeElement(KillSwitchesPage)} />

          {/* FEDERATION */}
          <Route path="/federation" element={routeElement(FederationOverviewPage)} />
          <Route path="/federation/graph" element={routeElement(GraphBuilderPage)} />
          <Route path="/federation/audit" element={routeElement(FederationAuditPage)} />

          {/* COMPLIANCE */}
          <Route path="/compliance" element={routeElement(ComplianceExportPage)} />

          {/* TIME-TRAVEL */}
          <Route path="/timetravel" element={routeElement(TimeTravelDebuggerPage)} />

          {/* INCIDENTS */}
          <Route path="/incidents" element={routeElement(IncidentsDashboardPage)} />

          {/* BUSINESS */}
          <Route path="/business" element={routeElement(BusinessDashboardPage)} />

          {/* Default redirect */}
          <Route path="/" element={<Navigate to="/command" replace />} />
          <Route path="*" element={<Navigate to="/command" replace />} />
        </Route>
      </Route>
    </Routes>
  )
}

export default App
