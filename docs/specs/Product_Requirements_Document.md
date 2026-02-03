# Product Requirements Document: Project Lancelot v5.0
**"The High-Context Autonomous Paladin"**

## 1. Product Vision
Lancelot is an autonomous AI agent designed to live *inside* the user's workspace, acting as a secure, high-context operational partner. Unlike traditional "Chat with PDF" tools (RAG), Lancelot leverages massive context windows to hold the entire relevant state of a project in memory, enabling reasoning, planning, and execution with full awareness.

**Core Philosophy:**
- **Context is King:** RAG is lossy. Long-context is deterministic.
- **Autonomy required Verification:** Agents that guess are dangerous. Agents that verify are useful.
- **Receipts are Truth:** Every action must produce a durable, auditable receipt.

## 2. Problem Statement
Legacy RAG-based agents suffer from "Retrieval Hallucination" — they miss critical context because they only see what they search for. They lack the ability to plan complex multi-step tasks or self-correct when a step fails.

## 3. Product Features (v5.0)

### 3.1 Core Architecture: The "Mind"
- **Context Environment:** 
    - Replaces vector databases.
    - Manages a 128k+ token window containing:
        - **Core Memories:** `USER.md`, `RULES.md`, `MEMORY_SUMMARY.md`.
        - **Short-Term History:** Recent chat and tool outputs.
        - **Active Files:** Explicitly loaded file contents.
- **Autonomous Loop:**
    - **Planner:** Breakdowns complex user goals into JSON-structured steps (`planner.py`).
    - **Executor:** Runs tools safely to achieve steps.
    - **Verifier:** Reviews execution output against the goal and triggers self-correction (`verifier.py`).

### 3.2 Unified Onboarding & Auth
- **Identity Bond:** Creates a persistent user profile (`USER.md`).
- **Auth Divergence:**
    - **API Version:** Supports Gemini API Key input.
    - **OAuth Version:** Auto-detects Google Application Default Credentials (ADC) for seamless enterprise integration.

### 3.3 Launchers
- **Lancelot-API:** Browser-based interface via Docker.
- **Lancelot-Oauth:** Native desktop window via `pywebview`.

### 3.4 Governance & Safety
- **Receipt System:** Every file read, command run, or LLM thought generates a JSON receipt (`receipts.py`).
- **Cognition Governor:** Limits daily token/tool usage to prevent runaways.
- **Sentry:** Mandatory permission gates for high-risk actions (CLI, File Delete).
- **SafeREPL:** Internalized Python execution for common ops (`ls`, `cat`, `grep`) to reduce shell injection risks.

## 4. User Stories
| ID | As a... | I want to... | So that... |
|----|---------|--------------|------------|
| US-1 | DevOps Engineer | Ask Lancelot to "Audit the repo" | He reads all relevant files into context and finds issues without keyword searching. |
| US-2 | User | Have Lancelot fix a bug | He Plans the fix, writes the code, Verification tests it, and commits it autonomously. |
| US-3 | Manager | Review Lancelot's actions | I can inspect the generated Receipts to see exactly what he did and why. |

## 5. Non-Functional Requirements
- **Determinism:** Context loading must be strictly ordered.
- **Latency:** Planning steps should take < 5s per generation.
- **Security:** No shell access allowed outside of SafeREPL whitelist.
- **Privacy:** Data stays local (Docker volume) or sent only to trusted LLM endpoints.

## 6. Deprecations
- **Vector RAG (ChromaDB):** Removed in favor of `ContextEnvironment`.
- **Legacy Chain-of-Thought:** Replaced by explicit `Planner`/`Verifier` nodes.
