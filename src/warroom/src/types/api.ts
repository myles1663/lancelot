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
// Crusader  (/crusader_status)
// ------------------------------------------------------------------

export interface CrusaderStatusResponse {
  crusader_mode: boolean
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
  status: string
  [key: string]: unknown
}

export interface SoulStatusResponse {
  active_version: string
  available_versions: string[]
  pending_proposals: SoulProposal[]
}

export interface SoulProposalActionResponse {
  status: string
  proposal_id: string
  active_version?: string
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
