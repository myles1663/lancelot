const screenshotModules = import.meta.glob("../../images/war-room-*.png", {
  eager: true,
  import: "default",
  query: "?url"
}) as Record<string, string>;

function screenshot(name: string): string {
  const path = `../../images/${name}`;
  const image = screenshotModules[path];
  if (!image) {
    throw new Error(`Missing walkthrough screenshot: ${name}`);
  }
  return image;
}

export type CaptureState = "current" | "representative" | "needs-refresh";

export interface WalkthroughScreen {
  id: string;
  area: string;
  title: string;
  route: string;
  image: string;
  captureState: CaptureState;
  summary: string;
  operatorFocus: string[];
  governance: string[];
  receipts: string[];
  degradedState: string;
  subsections: string[];
  nextStep: string;
}

export const captureStateLabels: Record<CaptureState, string> = {
  current: "Fresh live capture",
  representative: "Representative capture",
  "needs-refresh": "Refresh candidate"
};

export const screens: WalkthroughScreen[] = [
  {
    id: "command-center",
    area: "Command",
    title: "Command Center",
    route: "/war-room/command",
    image: screenshot("war-room-command-center.png"),
    captureState: "current",
    summary:
      "The operator starts governed work here. Commands enter the async execution path, progress cards expose classification and tool-flow state, and final responses link back to receipts.",
    operatorFocus: [
      "Confirm the active run state before issuing another command.",
      "Use pause, retry, cancel, and approval continuation controls only for the visible scope.",
      "Watch model lane and intent classification to understand cost and routing."
    ],
    governance: [
      "Requests are classified before execution.",
      "T2/T3 actions pause for operator approval.",
      "Runtime pause and emergency stop are control-plane actions, not chat prompts."
    ],
    receipts: [
      "Command runs emit chat lifecycle events.",
      "Approved or denied actions are linked to governance receipts.",
      "ToolFlow progress remains visible until the scoped decision resolves."
    ],
    degradedState:
      "A blocked run, missing provider, or stale progress card should send the operator to Health and Receipts before retrying.",
    subsections: ["Async run card", "Approval continuation", "Runtime pause", "Emergency stop"],
    nextStep: "Audit the run in Receipt Explorer after completion."
  },
  {
    id: "command-completed",
    area: "Command",
    title: "Command Run Completion",
    route: "/war-room/command",
    image: screenshot("war-room-command-center-completed.png"),
    captureState: "needs-refresh",
    summary:
      "The completed state shows how a finished governed workflow should look once the response, run status, and receipt references agree.",
    operatorFocus: [
      "Check that the terminal state matches the final message.",
      "Open receipt IDs directly from the response.",
      "Use retry only when the completed result is wrong or incomplete."
    ],
    governance: [
      "Completion does not erase approval history.",
      "Retry creates a new run rather than mutating the prior one.",
      "Late provider responses cannot overwrite an operator-cancelled run."
    ],
    receipts: [
      "Completion receipts should share the same quest or run lineage.",
      "Retries should point back to the source run.",
      "Failures should preserve the operator-visible reason."
    ],
    degradedState:
      "If the UI says completed but receipts do not exist, treat the workflow as suspect and inspect backend logs.",
    subsections: ["Final answer", "Receipt links", "Retry lineage", "Run status"],
    nextStep: "Open Receipt Explorer and trace the quest lineage."
  },
  {
    id: "health",
    area: "Operations",
    title: "Health Dashboard",
    route: "/war-room/health",
    image: screenshot("war-room-health-dashboard.png"),
    captureState: "current",
    summary:
      "Health is the first stop when the console looks wrong. It separates ready, disabled, uninitialized, and runtime-degraded subsystem states.",
    operatorFocus: [
      "Read specific degraded reasons before restarting anything.",
      "Distinguish local model loaded state from inference readiness.",
      "Check subsystem last-seen timestamps when data looks stale."
    ],
    governance: [
      "Health does not grant authority; it explains whether guarded paths are ready.",
      "Disabled features must remain visible as disabled rather than disappearing.",
      "Fail-closed dependencies should be disclosed here or in the owning panel."
    ],
    receipts: [
      "Health sweeps can be run by scheduled jobs.",
      "Scheduler health results are available through job history.",
      "Readiness failures inform operator decisions but are not action approvals."
    ],
    degradedState:
      "A degraded health tile should include the dependency, last error, and next diagnostic direction.",
    subsections: ["Subsystem readiness", "Local model roles", "Degradation reasons", "Health sweep timing"],
    nextStep: "Use the owning subsystem panel for the next diagnostic step."
  },
  {
    id: "governance",
    area: "Governance",
    title: "Governance Dashboard",
    route: "/war-room/governance",
    image: screenshot("war-room-governance-dashboard.png"),
    captureState: "current",
    summary:
      "This is the policy decision surface for risk distribution, pending approvals, and recent governance outcomes.",
    operatorFocus: [
      "Review pending approvals by capability, target, and risk reason.",
      "Watch for abnormal T3 spikes.",
      "Use recent decisions to verify policy consistency."
    ],
    governance: [
      "Approvals and denials are explicit operator decisions.",
      "Risk tier escalation must be explainable.",
      "Policy cache behavior should not hide decision failures."
    ],
    receipts: [
      "Approvals and denials write durable governance receipts.",
      "Recent decisions should line up with Receipt Explorer.",
      "Rejected actions should preserve denial reason and actor."
    ],
    degradedState:
      "If approval queues fail to load, the operator should assume approval state is unknown and avoid executing risky work.",
    subsections: ["Risk distribution", "Approval queue", "Decision log", "Policy cache"],
    nextStep: "Open Trust Ledger or APL when recurring approvals suggest a pattern."
  },
  {
    id: "soul-inspector",
    area: "Soul",
    title: "Soul Constitution Viewer",
    route: "/war-room/soul#constitution",
    image: screenshot("war-room-soul-constitution-viewer.png"),
    captureState: "current",
    summary:
      "The Constitution Viewer is where operators review active constraints, overlays, editable governance fields, behavior tests, proposals, and versions before activation.",
    operatorFocus: [
      "Confirm the active Soul version before changing policy.",
      "Review proposal diffs and customization fields before approval.",
      "Keep deployment-specific recipient and data-boundary values narrow."
    ],
    governance: [
      "Templates create amendment proposals instead of silently mutating the active Soul.",
      "Activation requires explicit operator approval.",
      "Soul changes influence downstream risk, connector, and approval behavior."
    ],
    receipts: [
      "Soul changes should be traceable through proposal and activation receipts.",
      "Behavior evaluator outcomes support review but do not replace approval.",
      "Rejected proposals should keep the rejection reason."
    ],
    degradedState:
      "If Soul loading or proposal activation is degraded, do not rely on the displayed policy until Health and logs confirm state.",
    subsections: ["Active Soul", "Template library", "Editable fields", "Amendment proposals"],
    nextStep: "Run behavior evaluator checks before activating a changed Soul."
  },
  {
    id: "soul-yaml-editor",
    area: "Soul",
    title: "Soul YAML Editor",
    route: "/war-room/soul#yaml-editor",
    image: screenshot("war-room-soul-yaml-editor.png"),
    captureState: "current",
    summary:
      "The YAML Editor exposes the raw Soul document for governed amendments when structured controls are not enough.",
    operatorFocus: [
      "Use raw YAML edits only when the structured fields cannot express the change.",
      "Review the proposed diff before approval.",
      "Keep deployment-specific recipients, data scopes, and kill-switch rules narrow."
    ],
    governance: [
      "Saving creates an amendment proposal rather than mutating the active Soul.",
      "Activation remains a separate approval step.",
      "The linter and monotonic narrowing checks should reject unsafe broadening."
    ],
    receipts: [
      "YAML edits should be traceable through proposal and activation receipts.",
      "Rejected changes should retain validation or operator rejection reasons.",
      "Activation receipts should reference the resulting Soul version."
    ],
    degradedState:
      "If raw content or validation is unavailable, do not approve a YAML amendment from memory or screenshots.",
    subsections: ["Raw YAML", "Proposal author", "Reason", "Validation", "Save proposal"],
    nextStep: "Return to Constitution Viewer and review the pending proposal before activation."
  },
  {
    id: "soul-template-library",
    area: "Soul",
    title: "Soul Template Library",
    route: "/war-room/soul#templates",
    image: screenshot("war-room-soul-template-library.png"),
    captureState: "current",
    summary:
      "The Template Library lets operators inspect industry Soul templates and apply one as a governed amendment proposal.",
    operatorFocus: [
      "Filter templates by industry and choose the narrowest fit.",
      "Inspect template YAML before applying.",
      "Treat template application as proposal creation, not activation."
    ],
    governance: [
      "Templates must not silently override the active Soul.",
      "Template proposals still need approval and activation.",
      "Template posture should be tested through evaluator scenarios before use."
    ],
    receipts: [
      "Template application should create proposal evidence.",
      "Activation should reference the selected template and resulting Soul version.",
      "Rejected template proposals should preserve operator rationale."
    ],
    degradedState:
      "If template details fail to load, do not apply a template from the list card alone.",
    subsections: ["Industry filters", "Template cards", "Template YAML", "Apply as proposal"],
    nextStep: "Run behavior contract checks before activating the template proposal."
  },
  {
    id: "soul-behavior",
    area: "Soul",
    title: "Soul Behavior Contract",
    route: "/war-room/soul#behavior-contract",
    image: screenshot("war-room-soul-behavior-contract.png"),
    captureState: "current",
    summary:
      "Evaluator and behavior-contract screens help operators test expected allowed, approval-required, and blocked outcomes before relying on a Soul change.",
    operatorFocus: [
      "Run at least one allowed, one approval-required, and one denied scenario.",
      "Compare evaluator output with the intended template posture.",
      "Treat mismatches as blockers for activation."
    ],
    governance: [
      "Evaluation is read-only and does not execute external work.",
      "Contracts document expected behavior for future regression checks.",
      "Admin-gated diagnostics prevent casual policy probing."
    ],
    receipts: [
      "Contract runs should produce auditable run status.",
      "Unexpected evaluator decisions should be linked to the proposal under review.",
      "Activation receipts should follow only after contract review."
    ],
    degradedState:
      "If evaluator diagnostics are unavailable, activate only after equivalent manual policy review.",
    subsections: ["Scenario inputs", "Expected outcome", "Evaluator result", "Contract history"],
    nextStep: "Return to Soul Inspector and apply only the validated amendment."
  },
  {
    id: "skills",
    area: "Skills",
    title: "Skills Panel",
    route: "/war-room/skills",
    image: screenshot("war-room-skills.png"),
    captureState: "current",
    summary:
      "The Skills panel manages proposed and installed skills through a governed review, approval, installation, and runtime inspection lifecycle.",
    operatorFocus: [
      "Inspect requested permissions, domains, vault keys, and risk posture.",
      "Review pipeline evidence before approving or installing.",
      "Use installed-skill inspection before leaving a new skill enabled."
    ],
    governance: [
      "Proposal approval and installation are separate gates.",
      "Installation revalidates reviewed artifact hashes.",
      "Enable and disable controls are protected by skills admin authorization."
    ],
    receipts: [
      "Skill toggle actions emit TOOL_ENABLED or TOOL_DISABLED receipts.",
      "Proposal decisions should preserve reviewer identity and reason.",
      "Pipeline stages provide evidence for install readiness."
    ],
    degradedState:
      "If registry or factory state is degraded, installed skill rows should not be treated as complete authority state.",
    subsections: ["Proposal queue", "Pipeline evidence", "Installed inventory", "Inspector modal"],
    nextStep: "Open the installed-skill inspector for the runtime manifest and source proposal link."
  },
  {
    id: "receipts",
    area: "Audit",
    title: "Receipt Explorer",
    route: "/war-room/receipts",
    image: screenshot("war-room-receipt-explorer.png"),
    captureState: "current",
    summary:
      "Receipt Explorer is the audit surface for reconstructing governed work across commands, approvals, tool calls, scheduler runs, and subsystem changes.",
    operatorFocus: [
      "Filter by quest, action, status, or time window.",
      "Trace parent-child links instead of reading isolated events.",
      "Use receipt detail to verify policy decision metadata."
    ],
    governance: [
      "Receipts are append-only evidence, not editable notes.",
      "Governed state changes should have durable receipt proof.",
      "Missing receipt chains are production defects."
    ],
    receipts: [
      "This is the primary receipt inspection screen.",
      "Parent IDs reveal decision and execution lineage.",
      "Failure receipts should preserve concrete error context."
    ],
    degradedState:
      "If receipt queries fail, pause high-risk operations until auditability is restored.",
    subsections: ["Receipt list", "Filters", "Detail drawer", "Chain navigation"],
    nextStep: "Use the source subsystem page to correct the behavior that produced a bad receipt."
  },
  {
    id: "trust",
    area: "Governance",
    title: "Trust Ledger",
    route: "/war-room/trust",
    image: screenshot("war-room-trust-ledger.png"),
    captureState: "current",
    summary:
      "Trust Ledger shows connector and capability history, including graduation proposals and revocation signals.",
    operatorFocus: [
      "Review evidence before accepting any trust graduation.",
      "Investigate revocation events before re-enabling broader authority.",
      "Watch cooldowns so repeated proposals do not become rubber stamps."
    ],
    governance: [
      "Trust changes alter future oversight requirements.",
      "Graduation requires evidence and operator review.",
      "Failures should reset or constrain trust as configured."
    ],
    receipts: [
      "Trust decisions and revocations should be receipt-backed.",
      "Evidence counts should align with successful action receipts.",
      "Declined proposals should preserve operator rationale."
    ],
    degradedState:
      "If trust evidence cannot be loaded, keep the stricter tier until proof returns.",
    subsections: ["Trust scores", "Graduation proposals", "Revocations", "Cooldowns"],
    nextStep: "Use Governance Dashboard to approve or deny current risky actions."
  },
  {
    id: "apl",
    area: "Governance",
    title: "Approval Pattern Learning",
    route: "/war-room/apl",
    image: screenshot("war-room-apl-learning.png"),
    captureState: "current",
    summary:
      "APL surfaces repeat approval patterns and proposes bounded automation rules without silently changing approval policy.",
    operatorFocus: [
      "Check confidence, evidence count, and proposed limits.",
      "Decline patterns that are too broad or sensitive.",
      "Watch daily and lifetime rule counters."
    ],
    governance: [
      "APL proposes rules; operators approve them.",
      "Never-automate constraints remain hard stops.",
      "Rule limits act as circuit breakers."
    ],
    receipts: [
      "Rule proposal decisions should be auditable.",
      "Auto-approval usage should still produce decision evidence.",
      "Declined proposals enter cooldown."
    ],
    degradedState:
      "If learning state is unavailable, do not assume recurring approvals are automated.",
    subsections: ["Detected patterns", "Rule proposals", "Active rules", "Never-automate list"],
    nextStep: "Review Governance Dashboard for pending decisions affected by APL."
  },
  {
    id: "memory",
    area: "Memory",
    title: "Memory Panel",
    route: "/war-room/memory",
    image: screenshot("war-room-memory.png"),
    captureState: "current",
    summary:
      "Memory Panel shows tier sizes, quarantined edits, core blocks, and links into governed memory management.",
    operatorFocus: [
      "Process quarantine regularly.",
      "Scrutinize core block edits.",
      "Watch token budget and retrieval exclusions."
    ],
    governance: [
      "Memory writes can be quarantined for review.",
      "Core identity edits require extra scrutiny.",
      "Memory decisions should preserve provenance."
    ],
    receipts: [
      "Approved or rejected memory edits should write receipts.",
      "Compile diagnostics can be tied to memory_compile receipt data.",
      "Quarantine events should preserve source context."
    ],
    degradedState:
      "If memory stores are unavailable, treat context and retrieval results as incomplete.",
    subsections: ["Tier overview", "Core blocks", "Quarantine", "Search"],
    nextStep: "Open Context Efficiency when retrieval quality or token budget looks wrong."
  },
  {
    id: "memory-lower-detail",
    area: "Memory",
    title: "Memory Lower Detail",
    route: "/war-room/memory#lower-detail",
    image: screenshot("war-room-memory-lower-detail.png"),
    captureState: "current",
    summary:
      "The lower Memory view exposes additional memory detail and longer lists that do not fit in the first viewport.",
    operatorFocus: [
      "Scan lower memory sections after reviewing summary counts.",
      "Confirm whether long lists are showing current or stale memory data.",
      "Use the manager page when an item requires action."
    ],
    governance: [
      "Lower-list inspection is read-only until the operator enters a managed action surface.",
      "Quarantined or sensitive items should remain clearly separated.",
      "Summary counts should agree with detail sections."
    ],
    receipts: [
      "Memory state changes should still be audited from the manager or backend receipts.",
      "Read-only inspection does not substitute for approval evidence.",
      "Compile receipts help explain why detail items entered context."
    ],
    degradedState:
      "If lower details do not load, treat memory summary counts as incomplete.",
    subsections: ["Lower lists", "Detail cards", "Memory continuity", "Manager handoff"],
    nextStep: "Open Governed Memory Manager for edits, approvals, or rollback."
  },
  {
    id: "memory-manager",
    area: "Memory",
    title: "Governed Memory Manager Sample",
    route: "/war-room/memory/manage",
    image: screenshot("war-room-memory-manager.png"),
    captureState: "representative",
    summary:
      "Memory Manager is shown as a representative top-section sample: core block editing, quarantine counts, and the first review records are visible without repeating the full queue.",
    operatorFocus: [
      "Use the top section to orient on operator, queue counts, and core block edit controls.",
      "Inspect provenance before approving sensitive edits.",
      "Treat repeated review cards as a sample pattern unless a specific item needs inspection."
    ],
    governance: [
      "Memory approval controls should preserve reviewer intent.",
      "Sensitive edits must retain source and reason metadata.",
      "The sample must show enough of the first review set to make quarantine safeguards clear."
    ],
    receipts: [
      "Memory review decisions should emit durable evidence.",
      "Approved changes should remain traceable to source content.",
      "Rejected changes should preserve rejection reason."
    ],
    degradedState:
      "If memory detail cannot load, do not approve pending memory changes from summary counts alone.",
    subsections: ["Core block editor", "Queue counts", "First review records", "Approval sample"],
    nextStep: "Jump to the bottom sample when audit or history state matters."
  },
  {
    id: "memory-manager-history",
    area: "Memory",
    title: "Memory Manager Bottom Sample",
    route: "/war-room/memory/manage#history",
    image: screenshot("war-room-memory-manager-history.png"),
    captureState: "representative",
    summary:
      "The bottom sample shows recent tiered memory actions and the commit-history panel after the repeated queue content has been skipped.",
    operatorFocus: [
      "Confirm a memory action landed before leaving the manager.",
      "Use rollback only when the committed change is incorrect.",
      "Compare affected targets with the intended edit scope."
    ],
    governance: [
      "Rollback is a governed action that creates new evidence.",
      "Commit history should preserve author, time, target, and status.",
      "Memory action history should not be edited in place."
    ],
    receipts: [
      "Commit records should align with memory receipts.",
      "Rollback commits should reference the source commit.",
      "Recent action rows provide audit orientation before opening receipts."
    ],
    degradedState:
      "If history is unavailable, avoid making additional memory changes until auditability returns.",
    subsections: ["Recent actions", "Commit history", "Affected targets", "Rollback"],
    nextStep: "Open Receipt Explorer when a memory action needs full audit reconstruction."
  },
  {
    id: "context-efficiency",
    area: "Memory",
    title: "Context Efficiency",
    route: "/war-room/memory/context",
    image: screenshot("war-room-context-efficiency.png"),
    captureState: "current",
    summary:
      "Context Efficiency explains how compiled context was assembled, budgeted, cached, and excluded for a given objective.",
    operatorFocus: [
      "Compare static, dynamic, and retrieved token use.",
      "Look for retrieval misses or unexpected exclusions.",
      "Use diagnostics before changing memory policy."
    ],
    governance: [
      "Compiled context is bounded by configured budgets.",
      "Sensitive or quarantined memory should not enter context.",
      "Cache eligibility must not bypass governance."
    ],
    receipts: [
      "memory_compile receipts can support context investigations.",
      "Exclusion reasons should be inspectable.",
      "Diagnostics should line up with visible memory state."
    ],
    degradedState:
      "If compile telemetry is missing, avoid claims about why the model did or did not remember something.",
    subsections: ["Budget use", "Memory hits", "Exclusions", "Cache eligibility"],
    nextStep: "Return to Memory Panel to correct source memory state."
  },
  {
    id: "context-diagnostic-results",
    area: "Memory",
    title: "Context Diagnostic Results",
    route: "/war-room/memory/context#results",
    image: screenshot("war-room-context-efficiency-diagnostic.png"),
    captureState: "current",
    summary:
      "The diagnostic result state shows token budget, retrieval misses, exclusions, reuse, and cache eligibility after an operator runs a context compile.",
    operatorFocus: [
      "Compare static, dynamic, and retrieval token use after a real objective.",
      "Inspect exclusion reasons before changing memory policy.",
      "Use the context ID to connect the UI result back to receipt evidence."
    ],
    governance: [
      "Diagnostics are inspection actions and should not alter approval policy.",
      "Sensitive or quarantined memory should remain excluded from compiled context.",
      "Cache eligibility must not bypass Soul or memory governance."
    ],
    receipts: [
      "The context ID should map to compile telemetry.",
      "memory_compile receipts should preserve token and exclusion evidence.",
      "Unexpected exclusions should be traceable to memory state or policy."
    ],
    degradedState:
      "If diagnostics fail or return incomplete telemetry, avoid claims about memory quality until receipts and backend logs are checked.",
    subsections: ["Context ID", "Token cards", "Memory hits", "Exclusions", "Cache reuse"],
    nextStep: "Open Receipt Explorer and inspect the matching memory_compile evidence."
  },
  {
    id: "connectors",
    area: "Integrations",
    title: "Connectors",
    route: "/war-room/connectors",
    image: screenshot("war-room-connectors.png"),
    captureState: "current",
    summary:
      "Connector Status shows configured integrations, credential health, rate limits, and recent connector activity.",
    operatorFocus: [
      "Check credential status before assuming a connector is broken.",
      "Watch rate-limit pressure and recent error rates.",
      "Confirm enabled state matches deployment policy."
    ],
    governance: [
      "Connector operations still pass through policy and risk gates.",
      "Vault failures should fail closed.",
      "Soul connector policies can constrain recipients and channels."
    ],
    receipts: [
      "Connector calls and denials should be receipt-backed.",
      "Credential values should never appear in receipts.",
      "Rate-limit or policy denials need clear failure evidence."
    ],
    degradedState:
      "If the connector vault is degraded, connector availability should be treated as unknown or unavailable.",
    subsections: ["Connector list", "Credential status", "Rate limits", "Recent errors"],
    nextStep: "Use Setup & Recovery for vault diagnostics when credentials fail."
  },
  {
    id: "cost",
    area: "Operations",
    title: "Cost Tracker",
    route: "/war-room/costs",
    image: screenshot("war-room-cost-tracker.png"),
    captureState: "current",
    summary:
      "Cost Tracker shows provider usage, local-vs-frontier routing, and provider authentication state.",
    operatorFocus: [
      "Confirm mounted CLI auth before starting provider OAuth.",
      "Watch local savings and frontier spend trends.",
      "Check provider status after container rebuilds."
    ],
    governance: [
      "Provider selection should preserve configured routing policy.",
      "OAuth and CLI auth state should be explicit.",
      "Cost awareness informs but does not override policy."
    ],
    receipts: [
      "Usage tracking is separate from action approval receipts.",
      "Provider failures should be visible in health or run status.",
      "Routing decisions can be inferred from run metadata."
    ],
    degradedState:
      "If provider state is missing after rebuild, check durable provider config before changing credentials.",
    subsections: ["Provider cards", "Usage counters", "Savings", "Auth status"],
    nextStep: "Use Health Dashboard if local or frontier readiness is degraded."
  },
  {
    id: "scheduler",
    area: "Operations",
    title: "Scheduler",
    route: "/war-room/scheduler",
    image: screenshot("war-room-scheduler.png"),
    captureState: "current",
    summary:
      "Scheduler manages background jobs, manual triggers, skip reasons, and run history.",
    operatorFocus: [
      "Review skip reasons before manually rerunning jobs.",
      "Confirm required readiness gates are satisfied.",
      "Use manual trigger as a diagnostic, not a policy bypass."
    ],
    governance: [
      "Scheduled jobs still pass capability and readiness gates.",
      "Approval-required work should skip or wait rather than self-authorize.",
      "Runtime pause blocks new scheduled work."
    ],
    receipts: [
      "Scheduled job runs and failures should produce run records.",
      "Skill-backed jobs can emit skill execution receipts.",
      "Skip reasons should remain visible."
    ],
    degradedState:
      "A stopped scheduler means automated maintenance may not be happening; check feature flags and runtime state.",
    subsections: ["Job list", "Run history", "Skip reasons", "Manual trigger"],
    nextStep: "Open Skills if a scheduled skill is failing."
  },
  {
    id: "kill-switches",
    area: "Operations",
    title: "Kill Switches",
    route: "/war-room/flags",
    image: screenshot("war-room-kill-switches.png"),
    captureState: "current",
    summary:
      "Kill Switches are operator controls for disabling governed subsystems without deleting their data.",
    operatorFocus: [
      "Confirm the blast radius before toggling.",
      "Use explicit reasons for emergency controls.",
      "Check whether a restart is required for startup-only flags."
    ],
    governance: [
      "Hot-toggleable subsystems start and stop through the runtime manager.",
      "Kill switch state persists across restarts.",
      "Master and per-server MCP kills share the same operator contract."
    ],
    receipts: [
      "Toggle actions should be receipt-backed.",
      "Emergency actions should preserve operator and reason.",
      "Subsystem shutdown errors must be visible."
    ],
    degradedState:
      "If a subsystem reports enabled but degraded, use Health before toggling repeatedly.",
    subsections: ["Feature flags", "Subsystem toggles", "MCP kills", "UAB status"],
    nextStep: "Use Health Dashboard to verify the result of a toggle."
  },
  {
    id: "setup-recovery",
    area: "Operations",
    title: "Setup and Recovery",
    route: "/war-room/setup",
    image: screenshot("war-room-setup-recovery.png"),
    captureState: "current",
    summary:
      "Setup and Recovery centralizes onboarding state, vault diagnostics, logs, configuration reload, backups, and destructive recovery controls.",
    operatorFocus: [
      "Use the least destructive recovery path first.",
      "Read vault and onboarding state before resetting anything.",
      "Treat danger-zone operations as irreversible operational events."
    ],
    governance: [
      "Destructive controls require confirmation.",
      "Credential values remain hidden.",
      "Setup state must survive ordinary container rebuilds."
    ],
    receipts: [
      "Destructive recovery actions should be audit logged.",
      "Vault resets should preserve archived evidence where supported.",
      "Configuration reload results should be visible."
    ],
    degradedState:
      "If onboarding regresses unexpectedly, inspect durable data before changing provider credentials.",
    subsections: ["System", "Data", "Logs and config", "Danger zone"],
    nextStep: "Return to Health Dashboard after any recovery operation."
  },
  {
    id: "tool-fabric",
    area: "Tools",
    title: "Tool Fabric and UAB",
    route: "/war-room/tools",
    image: screenshot("war-room-tool-fabric.png"),
    captureState: "current",
    summary:
      "Tool Fabric and UAB status show provider readiness, desktop bridge state, connected apps, and available control methods.",
    operatorFocus: [
      "Confirm the daemon is online before expecting desktop control.",
      "Review connected apps and framework methods.",
      "Treat fallback routes as lower-confidence than direct structured hooks."
    ],
    governance: [
      "Tool actions remain risk-classified.",
      "UAB is a governed bridge, not open-ended computer control.",
      "Provider health should block unsafe assumptions."
    ],
    receipts: [
      "Tool executions should produce tool receipts.",
      "UAB actions should preserve method and target context.",
      "Bridge failures should be surfaced, not swallowed."
    ],
    degradedState:
      "If UAB is offline, desktop actions should be unavailable or explicitly degraded.",
    subsections: ["Provider health", "UAB daemon", "Connected apps", "Framework methods"],
    nextStep: "Use Receipts to audit any tool action taken through this surface."
  },
  {
    id: "hive",
    area: "Agents",
    title: "HIVE Agent Mesh",
    route: "/war-room/hive",
    image: screenshot("war-room-hive-agent-mesh.png"),
    captureState: "current",
    summary:
      "HIVE surfaces scoped sub-agents, lifecycle state, task boundaries, and intervention controls.",
    operatorFocus: [
      "Review each agent task and action budget.",
      "Pause or kill agents with explicit intervention reasons.",
      "Use history to understand collapse reasons."
    ],
    governance: [
      "Sub-agents get bounded scopes.",
      "Interventions are operator actions.",
      "Runtime pause and emergency stop affect HIVE ingress and active agents."
    ],
    receipts: [
      "Spawns, interventions, completions, and collapses should be receipted.",
      "History should explain final state.",
      "Budget and scope violations should preserve evidence."
    ],
    degradedState:
      "If architect, lifecycle, registry, or receipt manager readiness is degraded, do not spawn new agents.",
    subsections: ["Agent table", "Task submission", "Intervention dialog", "History"],
    nextStep: "Open Receipt Explorer for agent lineage after a HIVE run."
  },
  {
    id: "incidents",
    area: "Incidents",
    title: "Incident Response",
    route: "/war-room/incidents",
    image: screenshot("war-room-incidents.png"),
    captureState: "current",
    summary:
      "Incident Response gives operators a focused surface for active alerts, response posture, and operational follow-up.",
    operatorFocus: [
      "Prioritize active incidents before routine subsystem work.",
      "Check severity, owner, and last update before taking action.",
      "Use incident state to decide whether runtime controls are warranted."
    ],
    governance: [
      "Incident response should not weaken approval or receipt requirements.",
      "Emergency controls still require explicit operator intent.",
      "Post-incident recovery should restore normal governed posture."
    ],
    receipts: [
      "Escalations and emergency actions should be audit-backed.",
      "Incident timeline entries should line up with subsystem receipts.",
      "Resolved incidents should preserve final disposition."
    ],
    degradedState:
      "If incident data is unavailable, check Health and avoid assuming the system is quiet.",
    subsections: ["Active incidents", "Severity", "Response state", "Timeline"],
    nextStep: "Use Kill Switches or Health only after the incident context is clear."
  },
  {
    id: "federation",
    area: "Federation",
    title: "Federation Overview",
    route: "/war-room/federation",
    image: screenshot("war-room-federation-overview.png"),
    captureState: "current",
    summary:
      "Federation Overview monitors local identity, peer health, transport state, propagation, divergence, and mesh readiness.",
    operatorFocus: [
      "Confirm self address and fingerprint before trusting peer data.",
      "Look for stale heartbeat or budget telemetry.",
      "Treat divergence as an operator attention event."
    ],
    governance: [
      "Federation does not bypass local policy.",
      "Signed peer traffic and allowlists constrain remote control.",
      "Soul propagation and reconciliation must be visible."
    ],
    receipts: [
      "Federated actions should still create local or peer receipts.",
      "Propagation and divergence events need traceable evidence.",
      "Remote detail failures should stay explicit."
    ],
    degradedState:
      "If remote detail is unavailable, inspect peer registration and signed federation auth before acting.",
    subsections: ["This instance", "Peer topology", "Runtime degradation", "Divergence"],
    nextStep: "Open Fleet Dashboard for multi-instance operational triage."
  },
  {
    id: "fleet",
    area: "Federation",
    title: "Fleet Dashboard",
    route: "/war-room/federation/fleet",
    image: screenshot("war-room-fleet-dashboard.png"),
    captureState: "current",
    summary:
      "Fleet Dashboard aggregates multi-instance health, pending approvals, trust proposals, budget status, and recent receipt-backed activity.",
    operatorFocus: [
      "Sort by needs-attention before opening individual instances.",
      "Use unified approval queue for cross-instance decisions.",
      "Open peer Command Center only after confirming identity and health."
    ],
    governance: [
      "Fleet actions proxy through governed federation paths.",
      "Approvals carry operator identity.",
      "Needs Attention is broader than simple health."
    ],
    receipts: [
      "Fleet activity is receipt-backed.",
      "Remote approvals and denials should create governance evidence.",
      "Missing activity means receipts or remote detail may be unavailable."
    ],
    degradedState:
      "Stale peer data should block remote decision-making until the peer is reachable again.",
    subsections: ["Instance cards", "Needs attention", "Unified approvals", "Fleet activity"],
    nextStep: "Open the selected instance Command Center for detailed action."
  },
  {
    id: "federation-graph",
    area: "Federation",
    title: "Graph Builder",
    route: "/war-room/federation/graph",
    image: screenshot("war-room-federation-graph.png"),
    captureState: "current",
    summary:
      "Graph Builder visualizes federation topology so operators can inspect peer relationships, edge status, and propagation paths.",
    operatorFocus: [
      "Confirm peer identity before trusting topology edges.",
      "Look for stale, divergent, or missing links.",
      "Use graph state to plan federation diagnostics."
    ],
    governance: [
      "Topology visibility does not grant remote authority.",
      "Soul compatibility and allowlists constrain federation edges.",
      "Graph changes must remain explainable through federation state."
    ],
    receipts: [
      "Peer registration and topology changes should have receipt evidence.",
      "Divergence events should connect back to federation receipts.",
      "Missing edges should be investigated through peer health."
    ],
    degradedState:
      "If graph data is stale or incomplete, treat federation topology as advisory only.",
    subsections: ["Topology graph", "Peer edges", "Compatibility state", "Propagation paths"],
    nextStep: "Open Federation Audit Trail for the evidence behind topology changes."
  },
  {
    id: "federation-audit",
    area: "Federation",
    title: "Federation Audit Trail",
    route: "/war-room/federation/audit",
    image: screenshot("war-room-federation-audit.png"),
    captureState: "current",
    summary:
      "Federation Audit Trail exposes peer events, cross-instance activity, and synchronization evidence for multi-instance operations.",
    operatorFocus: [
      "Filter audit events by peer, event type, and time window.",
      "Compare audit entries with local Receipt Explorer results.",
      "Investigate missing or unsigned federation events."
    ],
    governance: [
      "Cross-instance activity must remain attributable.",
      "Unsigned or incompatible events should fail closed.",
      "Audit trail gaps are production-readiness issues."
    ],
    receipts: [
      "Federated receipts should include instance and Soul version context.",
      "Audit entries should preserve remote peer identity.",
      "Propagation failures should be visible as failure evidence."
    ],
    degradedState:
      "If audit data is unavailable, avoid remote approvals or topology changes until evidence returns.",
    subsections: ["Peer events", "Sync status", "Receipt links", "Failure evidence"],
    nextStep: "Use Receipt Explorer for local chain detail when an audit row is suspicious."
  },
  {
    id: "a2a",
    area: "Federation",
    title: "A2A Protocol",
    route: "/war-room/a2a",
    image: screenshot("war-room-a2a-protocol.png"),
    captureState: "current",
    summary:
      "A2A surfaces agent-card identity, inbound/outbound task status, trust, and peer protocol readiness.",
    operatorFocus: [
      "Verify card identity before delegation.",
      "Check inbound permissions and blocked framework rules.",
      "Watch task status for approval holds."
    ],
    governance: [
      "Unknown or unverified callers are blocked by policy.",
      "T3 delegated work requires approval.",
      "Network allowlists constrain outbound delegation."
    ],
    receipts: [
      "Delegation, verification, and trust updates should be receipted.",
      "Failed remote tasks should preserve the failure source.",
      "Pinned-card changes need evidence."
    ],
    degradedState:
      "If card verification is stale or unavailable, do not delegate sensitive work.",
    subsections: ["Agent card", "Inbound tasks", "Outbound tasks", "Verification"],
    nextStep: "Use Federation Overview to inspect the wider peer mesh."
  },
  {
    id: "compliance-export",
    area: "Compliance",
    title: "Compliance Export",
    route: "/war-room/compliance",
    image: screenshot("war-room-compliance-export.png"),
    captureState: "current",
    summary:
      "Compliance Export packages governance, receipt, and operational evidence for external review without exposing secrets.",
    operatorFocus: [
      "Select the correct evidence window before exporting.",
      "Confirm redaction posture before sharing artifacts.",
      "Use exports as review packages, not editable records."
    ],
    governance: [
      "Exports must preserve audit integrity.",
      "Secrets and credential values should remain redacted.",
      "Compliance packages should reflect current receipt state."
    ],
    receipts: [
      "Export generation should be traceable.",
      "Included receipt ranges should be explicit.",
      "Failed exports should preserve error context."
    ],
    degradedState:
      "If export generation is degraded, do not substitute manual screenshots for compliance evidence.",
    subsections: ["Export scope", "Evidence window", "Redaction posture", "Package status"],
    nextStep: "Open Receipt Explorer to inspect any included chain before sharing."
  },
  {
    id: "time-travel",
    area: "Debugging",
    title: "Time-Travel Debugger",
    route: "/war-room/timetravel",
    image: screenshot("war-room-time-travel-debugger.png"),
    captureState: "current",
    summary:
      "Time-Travel Debugger helps operators reconstruct execution state from historical events when a live view is no longer enough.",
    operatorFocus: [
      "Use a narrow time or quest scope before investigating.",
      "Compare reconstructed state with receipt evidence.",
      "Avoid treating replay views as authority for new actions."
    ],
    governance: [
      "Historical reconstruction should be read-only.",
      "Replay analysis must not mutate runtime state.",
      "Debug views should preserve operator accountability."
    ],
    receipts: [
      "Replay points should correspond to receipt timestamps.",
      "State transitions should be explainable through event history.",
      "Missing events should be treated as audit defects."
    ],
    degradedState:
      "If replay data is incomplete, use backend logs and receipts before drawing conclusions.",
    subsections: ["Replay scope", "State reconstruction", "Event timeline", "Receipt correlation"],
    nextStep: "Return to the owning subsystem after identifying the failed transition."
  },
  {
    id: "overview",
    area: "Operations",
    title: "Overview Dashboard",
    route: "/war-room",
    image: screenshot("war-room-overview-dashboard.png"),
    captureState: "representative",
    summary:
      "The overview screen gives a compact orientation layer for runtime posture before an operator dives into subsystem pages.",
    operatorFocus: [
      "Use it as orientation, not final proof.",
      "Follow any degraded tile to the owning subsystem.",
      "Cross-check with Health for readiness details."
    ],
    governance: [
      "Overview status should reflect subsystem truth.",
      "Healthy-looking summaries must not hide disabled or degraded features.",
      "Operator action still belongs in the owning panel."
    ],
    receipts: [
      "Overview usually summarizes receipt-backed subsystems.",
      "Use Receipt Explorer for audit detail.",
      "Do not infer auditability from summary counts alone."
    ],
    degradedState:
      "If overview and subsystem pages disagree, trust the owner page and backend health payload.",
    subsections: ["Runtime summary", "Subsystem cards", "Attention items", "Quick links"],
    nextStep: "Open Health Dashboard for detailed readiness."
  }
];

export const captureBacklog = screens.filter((screen) => screen.captureState === "needs-refresh");
