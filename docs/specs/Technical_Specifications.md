# Technical Specifications: Project Lancelot v5.0

## 1. Technology Stack
- **Language:** Python 3.11+
- **Frameworks:** 
    - **UI:** Streamlit (War Room) + Pywebview (Native Wrapper)
    - **API:** FastAPI (Gateway)
    - **LLM:** Google GenAI SDK (`google-genai`)
- **Containerization:** Docker & Docker Compose
- **Data Persistence:** JSON-based local files (`lancelot_data/`)

## 2. Component Architecture

### 2.1 Orchestrator (`orchestrator.py`)
the central nervous system.
- **Role:** Routes messages, manages state (`ACTIVE`, `SLEEPING`), and coordinates specialized modules.
- **Key Method:** `chat(user_message)`
    1. Check Governance (Token Limits).
    2. Sanitize Input.
    3. Update Context History.
    4. Call LLM (Gemini 2.0 Flash) with `ContextEnvironment`.
    5. Parse "Confidence Score" and "Actions".
    6. Generate Receipt.

### 2.2 Context Environment (`context_env.py`)
*Replaces Legacy Vector Indexer.*
- **Logic:** Manages a deterministic list of `ContextItem` objects.
- **Budgeting:** Enforces `MAX_CONTEXT_TOKENS` (128k).
- **Persistence:** Autosaves chat logs to `chat_log.json`.
- **API:**
    - `read_file(path)`: Loads file content.
    - `search_workspace(query)`: Basic string matching (grep-like).
    - `get_file_outline(path)`: AST-based summary for code files.

### 2.3 Receipt System (`receipts.py`)
- **Storage:** Flat JSON files in `lancelot_data/receipts/`.
- **Schema:**
```json
{
  "id": "uuid",
  "timestamp": "iso8601",
  "action_type": "LLM_CALL | FILE_OP | TOOL_CALL",
  "action_name": "string",
  "inputs": {},
  "outputs": {},
  "status": "success | failure",
  "duration_ms": 123
}
```

### 2.4 Autonomous Loop (`planner.py`, `verifier.py`)
- **Planner:** Uses `gemini-2.0-flash` to generate a JSON Plan from a goal.
- **Verifier:** Uses `gemini-2.0-flash` with temperature 0.1 to audit execution outputs against step descriptions.

## 3. Security Implementation

### 3.1 Sentry (`mcp_sentry.py`)
Interceps tool calls based on a policy engine.
- **Pending:** Commands like `rm`, `mv` require user approval via Streamlit UI.
- **Denied:** Blacklisted commands or paths outside `lancelot_data`.

### 3.2 Cognition Governor (`security.py`)
- **Leaky Bucket Algorithm:** Tracks daily token and tool usage.
- **Thresholds:** defined in environment variables (e.g., `MAX_DAILY_TOKENS`).

### 3.3 SafeREPL (`orchestrator.py` internal)
- Implements `ls`, `cat`, `grep` using Python standard library (`os`, `glob`, `shutil`) to avoid `subprocess.run(shell=True)` risks.

## 4. Legacy Deprecations
- **Vector DB:** `indexer.py` / ChromaDB is deprecated. All memory is now explicit via `ContextEnvironment`.
- **Semantic Search:** Replaced by deterministic file reading and literal search.
