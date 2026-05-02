// ============================================================
// Lancelot War Room — API Response Types
// Mirrors every backend response shape for type-safe fetch calls
// ============================================================

// ------------------------------------------------------------------
// Common
// ------------------------------------------------------------------

export interface ApiError {
  error: string
  status: number
  detail?: string
  request_id?: string
}

// ------------------------------------------------------------------
// Health  (/health/*)
// ------------------------------------------------------------------

export interface HealthCheckResponse {
  status: string
  version: string
  components: Record<string, string>
  local_llm?: {
    loaded: boolean
    ready: boolean
    status: string
    last_error: string | null
    last_verified_at: string | null
    last_checked_at: string | null
    consecutive_failures: number
    last_smoke_elapsed_ms: number | null
    roles?: Record<string, LocalModelRoleStatus>
  }
  crusader_mode: boolean
  uptime_seconds: number
  error_count: number
  total_requests: number
  error_rate: number
}

export interface HealthReadyResponse {
  ready: boolean
  onboarding_state: string
  local_llm_ready: boolean
  local_llm_loaded: boolean
  local_llm_status: string
  local_llm_last_verified_at: string | null
  local_llm_last_checked_at: string | null
  local_llm_last_error: string | null
  local_llm_consecutive_failures: number
  local_llm_last_smoke_elapsed_ms: number | null
  scheduler_running: boolean
  last_health_tick_at: string | null
  last_scheduler_tick_at: string | null
  degraded_reasons: string[]
  timestamp: string | null
}

export interface HealthLiveResponse {
  status: string
}

export interface ReadinessResponse {
  ready: boolean
  components: Record<string, string>
}

// ------------------------------------------------------------------
// System  (/system/*)
// ------------------------------------------------------------------

export interface SystemStatusResponse {
  onboarding: {
    state: string
    flagship_provider: string
    credential_status: string
    local_model_status: string
    local_model_runtime_status: string
    local_model_runtime_ready: boolean
    local_model_runtime_loaded: boolean
    local_model_last_verified_at: string | null
    local_model_last_error: string | null
    is_ready: boolean
  }
  cooldown: {
    active: boolean
    remaining_seconds: number
    reason: string | null
  }
  runtime_pause: RuntimePauseStatusResponse
  model_usage_policy: ModelUsagePolicyResponse
  uptime_seconds: number
}

export interface RuntimePauseStatusResponse {
  paused: boolean
  reason: string | null
  source: string | null
  paused_at: string | null
  paused_by_operator_id: string | null
  paused_by_display_name: string | null
  paused_by_session_id: string | null
  resumed_at: string | null
  resumed_by_operator_id: string | null
  resumed_by_display_name: string | null
  resumed_by_session_id: string | null
  updated_at: string | null
}

export interface RuntimeEmergencyStopResponse extends RuntimePauseStatusResponse {
  stopped_hive_agents: number
  stopped_agent_ids: string[]
  execution_state: string
}

export interface ModelUsagePolicyResponse {
  local_execution_mode: string
  frontier_scrub_mode: string
  updated_at: number | null
  local_execution_available: boolean
  local_scrub_available: boolean
  availability_reason: string | null
  local_model_loaded: boolean
  local_model_ready: boolean
  local_model_status: string
  local_model_last_verified_at: string | null
  local_model_last_checked_at: string | null
  local_model_last_error: string | null
  local_model_consecutive_failures: number
  local_model_last_smoke_elapsed_ms: number | null
  local_model_roles?: Record<string, LocalModelRoleStatus> | {
    roles?: Record<string, LocalModelRoleStatus>
    scrub_priority?: string
  }
  frontier_scrub_fallback_active: boolean
  frontier_scrub_fallback_count: number
  last_frontier_scrub_fallback_at: number | null
  last_frontier_scrub_fallback_reason: string | null
}

// ------------------------------------------------------------------
// Chat  (/chat, /chat/upload)
// ------------------------------------------------------------------

export interface ChatResponse {
  response: string
  crusader_mode: boolean
  request_id: string
}

export interface LocalModelRoleStatus {
  configured?: boolean
  enabled?: boolean
  model?: string
  base_url?: string
  priority?: number
  ready?: boolean
  loaded?: boolean
  status?: string
  last_error?: string | null
  last_verified_at?: string | null
  last_checked_at?: string | null
  consecutive_failures?: number
  last_smoke_elapsed_ms?: number | null
}

export type ChatRunStatus = 'queued' | 'running' | 'blocked' | 'succeeded' | 'failed' | 'cancelled'

export interface ChatRunProgressEvent {
  phase: string
  message: string
  at: string
  elapsed_ms: number
  severity?: 'info' | 'warning' | 'error'
  degraded?: boolean
  degraded_reason?: string
  wait_reason?: string
}

export interface ChatRunReceiptProof {
  available: boolean
  receipt_count: number
  linked_run_count: number
  governed_tools: string[]
  approval_state: 'used' | 'required' | 'not_used' | 'unknown'
  degraded_mode: 'used' | 'not_used' | 'unknown'
  degraded_reasons: string[]
  outcome: ChatRunStatus
  error?: string
}

export interface ChatRunState {
  run_id: string
  request_id: string
  status: ChatRunStatus
  user: string
  channel: string
  session_id: string
  operator_id: string
  message_preview: string
  response: string
  error: string
  phase: string
  created_at: string
  updated_at: string
  started_at?: string | null
  completed_at?: string | null
  crusader_mode: boolean
  retry_of_run_id?: string
  retry_count?: number
  cancel_requested?: boolean
  cancel_reason?: string
  cancelled_at?: string | null
  progress_events?: ChatRunProgressEvent[]
  phase_timings_ms?: Record<string, number>
  total_elapsed_ms?: number | null
  last_progress_message?: string
  receipt_proof?: ChatRunReceiptProof | null
}

export interface ChatAsyncResponse extends ChatResponse {
  accepted: boolean
  status: ChatRunStatus
  run_id: string
  run: ChatRunState
}

export interface ChatRunCancelResponse {
  cancelled: boolean
  status: ChatRunStatus
  run_id: string
  run: ChatRunState
  request_id: string
}

export interface ChatRunsResponse {
  runs: ChatRunState[]
  count: number
}

export type ActiveWorkStatus = 'active' | 'blocked' | 'checkpointed' | 'completed' | 'failed' | 'cancelled'

export interface ActiveWorkItem {
  quest_id: string
  session_id: string
  operator_id: string
  channel: string
  objective: string
  status: ActiveWorkStatus
  phase: string
  current_step: string
  next_action: string
  blocker: string
  last_chat_run_id: string
  last_task_run_id: string
  last_receipt_id: string
  created_at: string
  updated_at: string
  metadata: Record<string, unknown>
}

export interface WorkLedgerEvent {
  event_id: string
  quest_id: string
  event_type: string
  summary: string
  receipt_id: string
  phase: string
  status: string
  created_at: string
  metadata: Record<string, unknown>
}

export interface WorkCheckpoint {
  checkpoint_id: string
  quest_id: string
  reason: string
  summary: string
  completed_work: string[]
  pending_work: string[]
  open_decisions: string[]
  files_touched: string[]
  approvals: string[]
  receipt_ids: string[]
  created_at: string
}

export interface ActiveWorkResponse {
  items: ActiveWorkItem[]
  count: number
}

export interface WorkItemResponse {
  item: ActiveWorkItem
  events: WorkLedgerEvent[]
  checkpoints: WorkCheckpoint[]
}

export interface WorkCheckpointResponse {
  checkpoint: WorkCheckpoint
  quest_id: string
  request_id: string
}

export interface WorkResumeResponse extends ChatAsyncResponse {
  source_quest_id: string
}

export interface ChatUploadResponse extends ChatResponse {
  files_received: number
}

// ------------------------------------------------------------------
// Crusader  (/crusader_status, /api/crusader/*)
// ------------------------------------------------------------------

export interface CrusaderStatusResponse {
  crusader_mode: boolean
  activated_at: string | null
  flag_overrides: number
  soul_override: string
  overridden_flags: string[]
}

export interface CrusaderActionResponse extends CrusaderStatusResponse {
  status: string
  message: string
}

// ------------------------------------------------------------------
// Onboarding  (/onboarding/*)
// ------------------------------------------------------------------

export interface OnboardingStatusResponse {
  state: string
  flagship_provider: string
  provider_mode: string
  credential_status: string
  local_model_status: string
  local_model_runtime_status: string
  local_model_runtime_ready: boolean
  local_model_runtime_loaded: boolean
  local_model_last_verified_at: string | null
  local_model_last_checked_at: string | null
  local_model_last_error: string | null
  local_model_consecutive_failures: number
  local_model_last_smoke_elapsed_ms: number | null
  is_ready: boolean
  cooldown_active: boolean
  cooldown_remaining: number
  last_error: string | null
  resend_count: number
  updated_at: string
}

export interface OnboardingCommandResponse {
  command: string
  response: string
  state: string
}

// ------------------------------------------------------------------
// Soul  (/soul/*)
// ------------------------------------------------------------------

export interface SoulProposal {
  proposal_id: string
  id?: string
  proposed_version?: string
  diff_summary?: string[]
  author?: string
  created_at?: string
  status: string
  proposed_yaml?: string
}

export interface SoulOverlayInfo {
  name: string
  feature_flag: string
  description: string
  risk_rules_count: number
  tone_invariants_count: number
  memory_ethics_count: number
  autonomy_additions: number
}

export interface SoulStatusResponse {
  active_version: string
  available_versions: string[]
  pending_proposals: SoulProposal[]
  active_overlays?: SoulOverlayInfo[]
}

export interface SoulProposalActionResponse {
  status: string
  proposal_id: string
  active_version?: string
}

export interface SoulAutonomyPosture {
  level: string
  description: string
  allowed_autonomous: string[]
  requires_approval: string[]
}

export interface SoulRiskRule {
  name: string
  description: string
  enforced: boolean
}

export interface SoulApprovalRules {
  default_timeout_seconds: number
  escalation_on_timeout: string
  channels: string[]
}

export interface SoulSchedulingBoundaries {
  max_concurrent_jobs: number
  max_job_duration_seconds: number
  no_autonomous_irreversible: boolean
  require_ready_state: boolean
  description: string
}

export interface SoulDocument {
  version: string
  mission: string
  allegiance: string
  autonomy_posture: SoulAutonomyPosture
  risk_rules: SoulRiskRule[]
  approval_rules: SoulApprovalRules
  tone_invariants: string[]
  memory_ethics: string[]
  scheduling_boundaries: SoulSchedulingBoundaries
}

export interface SoulContentResponse {
  soul: SoulDocument
  raw_yaml: string
  active_overlays?: SoulOverlayInfo[]
}

export interface SoulProposeResponse {
  proposal_id: string
  proposed_version: string
  diff_summary: string[]
  warnings: { rule: string; severity: string; message: string }[]
  status: string
}

export interface SoulTemplateMetadata {
  name: string
  display_name: string
  description: string
  industry: string
  version: string
  author: string
  tags: string[]
}

export interface SoulTemplateDetail {
  metadata: SoulTemplateMetadata
  soul_dict: Record<string, unknown>
  raw_yaml: string
}

export interface SoulTemplateListResponse {
  templates: SoulTemplateMetadata[]
  count: number
}

export interface SoulTemplateApplyResponse {
  proposal_id: string
  proposed_version: string
  diff_summary: string[]
  template_name: string
  template_version: string
  fields_customized: string[]
  status: string
}

// ------------------------------------------------------------------
// Memory  (/memory/*)
// ------------------------------------------------------------------

export interface CoreBlock {
  block_type: string
  content: string
  token_count: number
  token_budget: number
  status: string
  updated_at: string
  updated_by: string
  version: number
  confidence: number
}

export interface CoreBlocksResponse {
  blocks: Record<string, CoreBlock>
  total_tokens: number
}

export interface SearchResultItem {
  id: string
  tier: string
  title: string
  content: string
  confidence: number
  score: number
  tags: string[]
  namespace: string
}

export interface MemorySearchResponse {
  results: SearchResultItem[]
  total_count: number
  query: string
}

export interface RecentMemoryItem {
  id: string
  tier: string
  title: string
  content: string
  namespace: string
  confidence: number
  token_count: number
  created_at: string
  updated_at: string
  tags: string[]
}

export interface RecentMemoryResponse {
  items: RecentMemoryItem[]
  total_count: number
}

export interface BeginCommitResponse {
  commit_id: string
  status: string
}

export interface AddEditResponse {
  edit_id: string
  commit_id: string
}

export interface FinishCommitResponse {
  commit_id: string
  status: string
  edit_count: number
}

export interface RollbackResponse {
  rollback_commit_id: string
  rolled_back_commit_id: string
}

export interface MemoryCommitSummary {
  commit_id: string
  created_at: string
  created_by: string
  status: string
  message: string
  edit_count: number
  affected_targets: string[]
  has_core_edits: boolean
  receipt_id?: string | null
  rollback_of?: string | null
}

export interface MemoryCommitHistoryResponse {
  commits: MemoryCommitSummary[]
  total_count: number
}

export interface QuarantineItem {
  id: string
  tier: string
  title: string
  content: string
  status: string
  flagged_reason?: string | null
  detection_metadata?: Record<string, unknown>
}

export interface QuarantineResponse {
  core_blocks: Array<{
    block_type: string
    content: string
    updated_at: string
  }>
  items: QuarantineItem[]
}

export interface MemoryActionResponse {
  status: string
  item_id: string
  tier?: string | null
  block_type?: string | null
  reason: string
}

export interface CompileContextResponse {
  context_id: string
  token_estimate: number
  token_breakdown: Record<string, number>
  included_blocks: string[]
  included_memory_count: number
  excluded_count: number
}

export interface MemoryStatsResponse {
  index: {
    total_items: number
    items_by_tier: Record<string, number>
    tiers_available: string[]
  }
  core_blocks: {
    total_tokens: number
    budget_issues: unknown[]
  }
  gates: Record<string, unknown>
}

// ------------------------------------------------------------------
// Usage  (/usage/*)
// ------------------------------------------------------------------

export interface UsageSummary {
  [key: string]: unknown
}

export interface UsageSummaryResponse {
  usage: UsageSummary
  message?: string
}

export interface UsageLanesResponse {
  lanes: Record<string, unknown>
  message?: string
}

export interface UsageModelsResponse {
  models: Record<string, unknown>
  message?: string
}

export interface UsageSavingsResponse {
  savings: Record<string, unknown>
  message?: string
}

export interface UsageMonthlyResponse {
  monthly: Record<string, unknown>
  available_months?: string[]
  message?: string
}

// ------------------------------------------------------------------
// Tokens  (/tokens/*)
// ------------------------------------------------------------------

export interface ExecutionToken {
  id: string
  status: string
  [key: string]: unknown
}

export interface TokensListResponse {
  tokens: ExecutionToken[]
  total: number
  message?: string
}

export interface TokenGetResponse {
  token: ExecutionToken
}

export interface TokenRevokeResponse {
  status: string
  token_id: string
  reason: string
}

// ------------------------------------------------------------------
// Artifacts  (/warroom/artifacts/*)
// ------------------------------------------------------------------

export interface WarRoomArtifact {
  id: string
  session_id?: string
  [key: string]: unknown
}

export interface ArtifactsListResponse {
  artifacts: WarRoomArtifact[]
  total: number
}

export interface ArtifactGetResponse {
  artifact: WarRoomArtifact
}

export interface ArtifactStoreResponse {
  status: string
  artifact_count: number
}

// ------------------------------------------------------------------
// Router  (/router/*)
// ------------------------------------------------------------------

export interface RouterDecision {
  [key: string]: unknown
}

export interface RouterDecisionsResponse {
  decisions: RouterDecision[]
  total: number
  message?: string
}

export interface RouterStatsResponse {
  stats: Record<string, unknown>
  message?: string
}

// ------------------------------------------------------------------
// Setup & Recovery  (/api/setup/*)
// ------------------------------------------------------------------

export interface SystemInfoResponse {
  version: string
  uptime_seconds: number
  python_version: string
  platform: string
  hostname: string
  data_dir: { path: string; total_mb: number; used_mb: number }
  runtime_degraded?: boolean
  degraded_reasons?: string[]
  runtime_errors?: string[]
}

export interface VaultKeyEntry {
  key: string
  type: string
  created_at: string
}

export interface VaultKeysResponse {
  keys: VaultKeyEntry[]
  total: number
  message?: string
}

export interface VaultMaskedEntry {
  key: string
  type: string
  created_at: string
  masked_value: string
}

export interface VaultMaskedResponse {
  keys: VaultMaskedEntry[]
  total: number
  message?: string
}

export interface VaultStatusResponse {
  status: string
  message: string
  available: boolean
  key_configured: boolean
  key_source: string
  key_origin: string
  key_id: string | null
  metadata_present: boolean
  metadata_key_id: string | null
  suspected_key_mismatch: boolean
  has_primary: boolean
  has_backup: boolean
  primary_path: string
  backup_path: string
  metadata_path: string
  reset_backups_path: string
  entry_count: number
  primary_last_modified: string | null
  backup_last_modified: string | null
  metadata_last_modified: string | null
  last_error: string | null
}

export interface LogsResponse {
  lines: string[]
  file: string
  total_lines: number
}

export interface SetupActionResponse {
  status: string
  message?: string
  runtime_degraded?: boolean
  degraded_reasons?: string[]
  runtime_errors?: string[]
}

export interface VaultResetResponse extends SetupActionResponse {
  archived_files: string[]
  archive_dir: string
  restart_required: boolean
  vault_status: VaultStatusResponse
}

export interface ConfigReloadResponse {
  status: string
  results: Record<string, string>
}

export interface MemoryPurgeResponse {
  status: string
  purged_files: string[]
}

// ------------------------------------------------------------------
// Updates  (/api/updates/*)
// ------------------------------------------------------------------

export interface UpdateStatusResponse {
  current_version: string
  latest_version: string | null
  update_available: boolean
  severity: 'info' | 'recommended' | 'important' | 'critical' | null
  message: string | null
  changelog_url: string | null
  released_at: string | null
  checked_at: number | null
  check_error: string | null
  check_error_kind: 'network_unreachable' | 'blocked_by_policy' | 'manifest_http_error' | 'manifest_parse_error' | 'unexpected_error' | null
  check_state: 'unchecked' | 'up_to_date' | 'update_available' | 'offline' | 'failed'
  next_check_after: number | null
  operator_message?: string | null
  show_banner: boolean
}

// ------------------------------------------------------------------
// Skills  (/api/skills/*)
// ------------------------------------------------------------------

export interface SkillProposalSummary {
  id: string
  name: string
  description: string
  permissions: string[]
  risk: string
  source: string
  target_domains: string[]
  credential_keys: string[]
  approved_capabilities: string[]
  status: string
  pipeline_passed: boolean
  pipeline_failed_at_stage: string | null
  created_at: string
  approved_by: string | null
}

export interface SkillProposalDetail extends SkillProposalSummary {
  author: string
  credentials: Array<{
    vault_key: string
    type: string
    purpose: string
  }>
  manifest_yaml: string
  security_manifest_yaml: string
  execute_code: string
  test_code: string
  tests_status: string | null
  pipeline_stage_results: Record<string, unknown>
  artifact_hashes: Record<string, string>
  approved_at: string | null
  rejected_reason: string | null
  rejected_at: string | null
  installed_at: string | null
}

export interface SkillProposalsResponse {
  proposals: SkillProposalSummary[]
  total: number
}

export interface InstalledSkill {
  name: string
  version: string
  enabled: boolean
  ownership: string
}

export interface SkillsListResponse {
  skills: InstalledSkill[]
  total: number
}

// ------------------------------------------------------------------
// Tool Flow  (toolflow.* WebSocket events)
// ------------------------------------------------------------------

export interface ToolFlowStep {
  iteration: number
  toolName: string
  status: 'running' | 'success' | 'failed' | 'blocked'
  outputSummary?: string
  timestamp: number
}

export interface ToolFlowState {
  questId: string
  steps: ToolFlowStep[]
  status: 'running' | 'completed' | 'failed' | 'blocked'
  currentIteration: number
  maxIterations: number
}

// ------------------------------------------------------------------
// Chat Progress  (chat.progress WebSocket events)
// ------------------------------------------------------------------

export interface ChatProgressState {
  questId?: string | null
  phase: string
  message: string
  timestamp: number
  severity?: 'info' | 'warning' | 'error'
  degraded?: boolean
  degradedReason?: string
  waitReason?: string
}

// ------------------------------------------------------------------
// Action Cards  (actioncard_* WebSocket events + REST API)
// ------------------------------------------------------------------

export type ActionCardType = 'approval' | 'confirmation' | 'choice' | 'info'

export type ActionCardButtonStyle = 'primary' | 'danger' | 'secondary'

export interface ActionCardButton {
  id: string
  label: string
  style: ActionCardButtonStyle
}

export interface ActionCardData {
  cardId: string
  cardType: ActionCardType
  questId?: string | null
  sourceSystem?: string
  sourceItemId?: string
  title: string
  description: string
  buttons: ActionCardButton[]
  resolved: boolean
  resolvedAction?: string
  resolvedChannel?: string
  resolutionConfirmed?: boolean
  presentedAt: number
  resolvedAt?: number
}

export interface ActionCardsPendingResponse {
  cards: ActionCardData[]
  count: number
}

export interface ActionCardResolveResponse {
  status: string
  message: string
}
