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
  crusader_mode: boolean
  uptime_seconds: number
}

export interface HealthReadyResponse {
  ready: boolean
  onboarding_state: string
  local_llm_ready: boolean
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
    is_ready: boolean
  }
  cooldown: {
    active: boolean
    remaining_seconds: number
    reason: string | null
  }
  uptime_seconds: number
}

// ------------------------------------------------------------------
// Chat  (/chat, /chat/upload)
// ------------------------------------------------------------------

export interface ChatResponse {
  response: string
  crusader_mode: boolean
  request_id: string
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
  credential_status: string
  local_model_status: string
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

export interface QuarantineItem {
  id: string
  tier: string
  title: string
  content: string
  status: string
}

export interface QuarantineResponse {
  core_blocks: Array<{
    block_type: string
    content: string
    updated_at: string
  }>
  items: QuarantineItem[]
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
  index: Record<string, unknown>
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

export interface LogsResponse {
  lines: string[]
  file: string
  total_lines: number
}

export interface SetupActionResponse {
  status: string
  message?: string
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
  status: string
  created_at: string
  approved_by: string | null
}

export interface SkillProposalDetail extends SkillProposalSummary {
  manifest_yaml: string
  execute_code: string
  test_code: string
  tests_status: string | null
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
