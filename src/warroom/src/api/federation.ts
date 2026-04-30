// ============================================================
// Federation API Client
// Graph Builder, Kill Switch, Soul Propagation, Cost, Audit
// ============================================================

import { apiGet, apiPost, apiPut, apiDelete } from './client'

// ── Types ────────────────────────────────────────────────────

export interface FederationStatus {
  enabled: boolean
  instance_id: string
  fingerprint: string
  public_key: string
  deployment_mode: string
  peer_count: number
  soul_consistency: string
  active_propagations?: Array<Record<string, unknown>>
  cost_threshold: string
  self_address: string
  transport_ready?: boolean
  transport_started?: boolean
  heartbeat_mesh_running?: boolean
  cost_reporter_running?: boolean
  runtime_degraded?: boolean
  degraded_reasons?: string[]
  runtime_errors?: string[]
  subscription_status?: Record<string, string>
  subscription_stream_outcome?: Record<string, string>
  subscription_stream_errors?: Record<string, string>
  circuit_breaker_summary?: {
    closed: number
    open: number
    half_open: number
  }
  stale_instance_ids?: string[]
}

export interface FederationSettings {
  instance_id: string
  fingerprint: string
  public_key: string
  self_address: string
  deployment_mode: string
  restart_required: boolean
}

export interface FederationPeer {
  instance_id: string
  fingerprint: string
  address: string
  role: string
  health: string
  last_heartbeat_at: string | null
}

export interface FederationHealthSummary {
  total_peers: number
  healthy: number
  warning: number
  critical: number
  lost: number
  deployment_mode: string
  runtime_degraded?: boolean
  degraded_reasons?: string[]
  transport_started?: boolean
  heartbeat_mesh_running?: boolean
  cost_reporter_running?: boolean
  subscription_status?: Record<string, string>
  subscription_stream_outcome?: Record<string, string>
  subscription_stream_errors?: Record<string, string>
  circuit_breaker_summary?: {
    closed: number
    open: number
    half_open: number
  }
  stale_instance_ids?: string[]
  divergence_state?: string
  active_propagation_count?: number
}

export type FleetInstanceState = 'healthy' | 'attention' | 'critical' | 'paused'

export interface FleetDashboardConfig {
  enabled: boolean
  poll_interval_s: number
  stream_interval_s: number
  max_recent_activity_items: number
  card_sort_order: string
  show_fleet_activity_feed: boolean
  activity_feed_max_events: number
}

export interface FleetSummary {
  total_instances: number
  instances_needing_attention: number
  critical_instances: number
  lost_instances: number
  paused_instances: number
  pending_approvals: number
  trust_proposals: number
  active_agents: number
  fleet_cost_utilization_pct: number
  budget_threshold: string
  soul_consistency: string
}

export interface FleetDashboardInstance {
  instance_id: string
  instance_short_id: string
  name: string
  role: string
  address: string
  command_center_url: string
  is_self: boolean
  state: FleetInstanceState
  health: string
  heartbeat_state: string
  heartbeat_age_s: number | null
  last_heartbeat_at: string | null
  soul_version_hash: string
  soul_matches_root: boolean | null
  budget_utilization_pct: number
  budget_threshold: string
  active_agents: number
  paused_agents: number
  pending_approvals: number
  trust_proposals: number
  recent_activity: string
  recent_activity_at: string | null
  attention_reasons: string[]
  runtime_errors: string[]
  detail_status: string
  paused: boolean
  pause_reason: string | null
}

export interface FleetApproval {
  id: string
  instance_id: string
  instance_name: string
  type: string
  action_name: string
  risk_tier: string
  capability: string
  context: string
  created_at: string
  waiting_since: string
}

export interface FleetTrustProposal {
  id: string
  instance_id: string
  instance_name: string
  capability: string
  scope: string
  current_tier: number
  proposed_tier: number
  consecutive_successes: number
  status: string
  created_at: string
}

export interface FleetActivityEvent {
  id: string
  timestamp: string
  instance_id: string
  instance_name: string
  event_type: string
  description: string
  operator: string
  status: string
}

export interface FleetDashboardError {
  instance_id: string
  message: string
}

export interface FleetDashboardSnapshot {
  enabled: boolean
  disabled_reason: string
  generated_at: string
  command_center_path: string
  dashboard: FleetDashboardConfig
  fleet: FleetSummary
  instances: FleetDashboardInstance[]
  approvals: FleetApproval[]
  trust_proposals: FleetTrustProposal[]
  activity: FleetActivityEvent[]
  errors: FleetDashboardError[]
}

export interface FleetDecisionResponse {
  success: boolean
  decision: 'approve' | 'deny'
  instance_id: string
  approval_id: string
  result: Record<string, unknown>
  remote?: Record<string, unknown>
}

// Graph Builder types
export interface TopologyNode {
  node_id: string
  instance_name: string
  endpoint: string
  federation_identity_public_key: string
  fingerprint: string
  instance_role: string
  soul_source_mode: string
  soul_version: string
  soul_version_hash: string
  connection_status: string
  hive_config: {
    enabled: boolean
    max_concurrent_agents: number
    default_task_timeout: number
    max_actions_per_agent: number
    uab_enabled: boolean
  }
  budget_config: {
    daily_ceiling_usd: number
    warning_pct: number
    critical_pct: number
  }
  position: { x: number; y: number }
  timezone: string
  is_local: boolean
  metadata: Record<string, unknown>
}

export interface DimensionResult {
  dimension: string
  state: string
  report: string
  resolution_options: string[]
}

export interface TopologyEdge {
  edge_id: string
  source_node_id: string
  target_node_id: string
  relationship_type: string
  trigger_condition: string
  priority: number
  compatibility_state: string
  dimension_results: DimensionResult[]
  yellow_acknowledgments: Array<{
    operator: string
    timestamp: string
    condition: string
    note: string
  }>
  resolution_history: Array<{
    conflict_type: string
    resolution_selected: string
    operator: string
    timestamp: string
  }>
  handoff_contract: {
    context_and_assumptions: Array<{
      text: string
      assumption_type: string
      criticality: string
    }>
    success_criteria: string[]
    data_payload_schema: Record<string, unknown>
    soul_context_constraints: Record<string, unknown>
    template_id: string | null
  }
  metadata: Record<string, unknown>
}

export interface TopologyDocument {
  topology_id: string
  topology_name: string
  version: number
  version_hash: string
  deployment_mode: string
  nodes: TopologyNode[]
  edges: TopologyEdge[]
  created_at: string
  updated_at: string
  deployed_at: string | null
  created_by: string
  metadata: Record<string, unknown>
}

export interface TopologyVersion {
  version: number
  version_hash: string
  topology_name: string
  node_count: number
  edge_count: number
  created_at: string
  updated_at: string
  deployed_at: string | null
}

export interface ValidationResult {
  edge_count: number
  results: Record<string, {
    state: string
    dimensions: DimensionResult[]
  }>
}

export interface DeploymentGateResult {
  deployable: boolean
  blocking_edges: string[]
  warning_edges: string[]
  unacknowledged_yellows: string[]
  report: string
}

// Kill Switch types
export interface KillCommand {
  command_id: string
  command_type: string
  authority: string
  issuer_id: string
  reason: string
  state: string
  issued_at: string
  completed_at: string | null
  lifted_at: string | null
  lifted_by: string | null
  target_instance_id: string | null
  target_agent_id: string | null
  target_feature: string | null
  targets: Array<{
    instance_id: string
    ack_state: string
    ack_at: string | null
    agents_killed: number
  }>
}

// Cost types
export interface FederationCostAggregate {
  total_actual_usd: number
  total_projected_usd: number
  total_ceiling_usd: number
  utilization_pct: number
  total_active_spawns: number
  total_spawn_cost_rate_usd_hr: number
  instance_count: number
  highest_utilization_instance: string
  highest_utilization_pct: number
  threshold: string
}

export interface InstanceCostData {
  instance_id: string
  actual_today_usd: number
  projected_today_usd: number
  daily_ceiling_usd: number
  utilization_pct: number
  projected_utilization_pct: number
  active_spawns: number
  spawn_cost_rate_usd_hr: number
  total_tokens_today: number
  updated_at: string
}

// Audit types
export interface AuditEntry {
  entry_id: string
  event_type: string
  instance_id: string
  federation_quest_id: string
  timestamp: string
  soul_version_hash: string
  risk_tier: string
  details: Record<string, unknown>
  related_entry_ids: string[]
}

export interface ForensicTimeline {
  quest_id: string
  instance_ids: string[]
  start_time: string
  end_time: string
  total_entries: number
  contradictions_found: number
  instances_involved: number
  entries: AuditEntry[]
}

export interface AuditSummary {
  total_entries: number
  unique_quests: number
  unique_instances: number
  event_type_counts: Record<string, number>
}

// Contradiction types
export interface Contradiction {
  contradiction_id: string
  federation_quest_id: string
  source_instance_id: string
  target_instance_id: string
  source_receipt_id: string
  target_receipt_id: string
  edge_id: string
  category: string
  severity: string
  state: string
  description: string
  assumption_text: string
  expected: string
  actual: string
  detected_at: string
  resolved_at: string | null
  resolution_action: string
}

// ── Federation Status ────────────────────────────────────────

export async function fetchFederationStatus(): Promise<FederationStatus> {
  return apiGet<FederationStatus>('/api/federation/status')
}

export async function fetchFederationSettings(): Promise<FederationSettings> {
  return apiGet<FederationSettings>('/api/federation/settings')
}

export async function updateFederationSettings(selfAddress: string): Promise<{
  saved: boolean
  self_address: string
  restart_required: boolean
  message: string
}> {
  return apiPut('/api/federation/settings', { self_address: selfAddress })
}

export async function fetchFederationPeers(): Promise<FederationPeer[]> {
  return apiGet<FederationPeer[]>('/api/federation/peers')
}

export async function fetchFederationHealth(): Promise<FederationHealthSummary> {
  return apiGet<FederationHealthSummary>('/api/federation/health')
}

export async function fetchFederationDashboard(): Promise<FleetDashboardSnapshot> {
  return apiGet<FleetDashboardSnapshot>('/api/federation/dashboard')
}

function encodePathSegment(value: string): string {
  return encodeURIComponent(value)
}

export function subscribeFederationDashboard(
  onSnapshot: (snapshot: FleetDashboardSnapshot) => void,
  onError?: (error: Error) => void,
  onConnectionChange?: (connected: boolean) => void,
): () => void {
  const source = new EventSource('/api/federation/dashboard/stream', { withCredentials: true })

  source.onopen = () => {
    onConnectionChange?.(true)
  }
  source.onerror = () => {
    onConnectionChange?.(false)
    onError?.(new Error('Fleet dashboard stream disconnected'))
  }
  source.addEventListener('snapshot', (event) => {
    try {
      onSnapshot(JSON.parse((event as MessageEvent<string>).data) as FleetDashboardSnapshot)
    } catch (error) {
      onError?.(error instanceof Error ? error : new Error(String(error)))
    }
  })
  source.addEventListener('dashboard_error', (event) => {
    try {
      const payload = JSON.parse((event as MessageEvent<string>).data) as { error?: string }
      onError?.(new Error(payload.error || 'Fleet dashboard stream snapshot failed'))
    } catch {
      onError?.(new Error('Fleet dashboard stream snapshot failed'))
    }
  })

  return () => source.close()
}

export async function approveFederationDashboardApproval(
  instanceId: string,
  approvalId: string,
  reason: string,
): Promise<FleetDecisionResponse> {
  return apiPost<FleetDecisionResponse>(
    `/api/federation/dashboard/instances/${encodePathSegment(instanceId)}/approvals/${encodePathSegment(approvalId)}/approve`,
    { reason },
  )
}

export async function denyFederationDashboardApproval(
  instanceId: string,
  approvalId: string,
  reason: string,
): Promise<FleetDecisionResponse> {
  return apiPost<FleetDecisionResponse>(
    `/api/federation/dashboard/instances/${encodePathSegment(instanceId)}/approvals/${encodePathSegment(approvalId)}/deny`,
    { reason },
  )
}

// ── Graph Builder ────────────────────────────────────────────

export async function fetchActiveTopology(): Promise<TopologyDocument> {
  return apiGet<TopologyDocument>('/api/federation/graph/topologies/active')
}

export async function createTopology(name: string): Promise<{ topology_id: string; version: number }> {
  return apiPost('/api/federation/graph/topologies', { topology_name: name })
}

export async function deleteActiveTopology(): Promise<{ deleted: boolean }> {
  return apiDelete('/api/federation/graph/topologies/active')
}

export async function fetchTopologyVersions(): Promise<{ versions: TopologyVersion[] }> {
  return apiGet('/api/federation/graph/topologies/versions')
}

export async function addNode(node: {
  node_id: string
  instance_name: string
  endpoint?: string
  federation_identity_public_key?: string
  fingerprint?: string
  instance_role?: string
  soul_source_mode?: string
  soul_version?: string
  soul_version_hash?: string
  hive_enabled?: boolean
  hive_max_agents?: number
  hive_uab_enabled?: boolean
  budget_daily_ceiling_usd?: number
  is_local?: boolean
}): Promise<{ node_id: string; node_count: number }> {
  return apiPost('/api/federation/graph/nodes', node)
}

export async function removeNode(nodeId: string): Promise<{ removed_node: string; removed_edges: string[] }> {
  return apiDelete(`/api/federation/graph/nodes/${nodeId}`)
}

export async function updateNode(nodeId: string, updates: Record<string, unknown>): Promise<{ updated: string }> {
  return apiPut(`/api/federation/graph/nodes/${nodeId}`, updates)
}

export async function addEdge(edge: {
  source_node_id: string
  target_node_id: string
  relationship_type?: string
}): Promise<{ edge_id: string; compatibility_state: string; edge_count: number }> {
  return apiPost('/api/federation/graph/edges', edge)
}

export async function removeEdge(edgeId: string): Promise<{ removed_edge: string }> {
  return apiDelete(`/api/federation/graph/edges/${edgeId}`)
}

export async function validateTopology(): Promise<ValidationResult> {
  return apiPost('/api/federation/graph/validate')
}

export async function validateEdge(edgeId: string): Promise<{ edge_id: string; state: string; dimensions: DimensionResult[] }> {
  return apiPost(`/api/federation/graph/validate/edge/${edgeId}`)
}

export async function acknowledgeYellow(edgeId: string, operator: string, note: string): Promise<{ acknowledged: boolean }> {
  return apiPost(`/api/federation/graph/edges/${edgeId}/acknowledge`, { operator, note })
}

export async function checkDeploymentGate(): Promise<DeploymentGateResult> {
  return apiPost('/api/federation/graph/deployment-gate')
}

export async function deployTopology(): Promise<{ deployed: boolean; version: number; deployed_at: string }> {
  return apiPost('/api/federation/graph/deploy')
}

export async function saveTopologyVersion(): Promise<{ version: number; version_hash: string }> {
  return apiPost('/api/federation/graph/topologies/active/save-version')
}

export async function updateHandoffContract(edgeId: string, contract: {
  success_criteria?: string[]
  data_payload_schema?: Record<string, unknown>
  soul_context_constraints?: Record<string, unknown>
}): Promise<{ updated: boolean }> {
  return apiPut(`/api/federation/graph/edges/${edgeId}/contract`, contract)
}
