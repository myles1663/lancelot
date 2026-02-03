# Functional Specifications: Project Lancelot v5.0

## 1. System Overview
Lancelot v5.0 is a High-Context Autonomous Agent. It functions as a "Paladin" -- a proactive, protective partner that operates within the user's secure perimeter (Docker). It rejects the stateless "Chat" paradigm in favor of a stateful "War Room" experience.

## 2. System Actors
- **Commander (User):** Defines high-level goals and approves Sentry requests.
- **Paladin (Lancelot Core):** Orchestrates context, safety, and execution.
- **Strategist (Planner):** Decomposes goals into logical steps.
- **Inquisitor (Verifier):** Audits step outputs for success criteria.

## 3. Core Functional Areas

### FA-01: Context Environment (The "Mind")
*Replacing Legacy RAG.*
- **F-01.1 Deterministic Context:** The system must load critical files (`USER.md`, `RULES.md`) at the top of every prompt context.
- **F-01.2 Explicit File Loading:** Users can ask the agent to "Read [file]" which loads the file content into the `ContextItem` registry, decrementing the token budget.
- **F-01.3 Token Budgeting:** System enforces a hard limit (default 128k tokens). If exceeded, Least Recently Used (LRU) files are evicted or summarized.

### FA-02: Autonomous Loop
- **F-02.1 Planning:** Upon receiving a complex goal (>50 tokens or strict keywords), the system invokes the `Planner` to generate a JSON step list.
- **F-02.2 Execution:** The `Executor` runs steps sequentially.
- **F-02.3 Verification:** After each step, the `Verifier` analyzes output.
    - If Success: Context is updated, proceed to next step.
    - If Failure: `Verifier` suggests correction, `Executor` retries (max 3 attempts).

### FA-03: Receipt System (Accountability)
- **F-03.1 Receipt Generation:** Every discrete action (LLM call, File Read, Command) generates a JSON Receipt.
- **F-03.2 Traceability:** Receipts link parent/child actions, allowing full reconstruction of a thought process.
- **F-03.3 Persistence:** Receipts are stored in `lancelot_data/receipts/` and indexed for "Short-Term Memory".

### FA-04: Unified Onboarding
- **F-04.1 Identity Bond:** System requires a name to create `USER.md`.
- **F-04.2 Authentication Fork:**
    - **API Mode:** Prompts for `GEMINI_API_KEY`.
    - **OAuth Mode:** Scans for `application_default_credentials.json` (ADC).
- **F-04.3 Comms Setup:** Configures Google Chat/Telegram webhooks.

### FA-05: Crusader Mode (high-Agency)
- **F-05.1 Triggers:** Active upon "Engage Crusader" button or "Crusader" keyword.
- **F-05.2 Behavior:**
    - Disables "Draft Mode" (unless extremely low confidence).
    - Increases tool autonomy (Sentry auto-approves low-risk file ops).
    - Uses "Decisive" system prompt injection.

### FA-06: SafeREPL (Internal Execution)
- **F-06.1 Supported Commands:** `ls`, `cat`, `grep`, `find`, `cp`, `mv`.
- **F-06.2 Implementation:** Commands run via internal Python functions (`shutil`, `os`, `glob`) rather than spawning shell subprocesses, mitigating shell injection risks.

## 4. Interfaces
- **War Room (Streamlit):** The primary command center.
- **Chat Interface:** Continuous scroll, distinct from "Logs".
- **Neural Audit:** Timeline view of Receipts.
- **Status Dashboard:** Real-time health, token usage, and mode (Shield/Crusader).
