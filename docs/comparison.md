# How Lancelot Compares

A factual comparison of Lancelot against the current AI agent landscape. Not a feature matrix — a design philosophy comparison.

We acknowledge where competitors are stronger. We explain why we made different choices. The goal is to help you decide whether Lancelot's approach fits your needs.

---

## The Core Difference

Most agent frameworks optimize for **flexibility and developer ergonomics**. Lancelot optimizes for **governance, verification, and reversibility**.

This is a genuine tradeoff. Frameworks give you more freedom to build quickly. Lancelot gives you more guarantees about what your agent can and cannot do. Choose based on which problem you actually have.

---

## Comparison Framework

We compare across seven dimensions:

1. **Governance** — How is agent behavior constrained?
2. **Verification** — How are action outcomes validated?
3. **Memory** — How does the agent maintain and protect state?
4. **Observability** — Can you see and audit everything the agent did?
5. **Tool Security** — How are tools constrained and sandboxed?
6. **Extensibility** — How do you add new capabilities?
7. **Recovery** — What happens when something goes wrong?

---

## OpenAI Agents SDK (formerly Swarm)

**What it is:** OpenAI's agent framework for building multi-agent systems with handoffs.

**What it's good at:** Simple agent composition, clean Python API, tight integration with OpenAI models, low barrier to entry.

**How it differs from Lancelot:**

| Dimension | OpenAI Agents SDK | Lancelot |
|-----------|------------------|----------|
| **Governance** | Model-level instructions via system prompts. No enforcement layer — the model decides whether to follow instructions. | Constitutional document (Soul) enforced by code outside the model. Model cannot override governance regardless of prompt. |
| **Verification** | No built-in verification. Developer must implement their own checks. | Mandatory Planner/Executor/Verifier pipeline. Results are checked, not assumed. |
| **Memory** | Conversation history only. No structured memory, no quarantine, no rollback. | Four-tier memory with commit-based edits, quarantine for risky writes, and exact rollback. |
| **Observability** | Basic tracing via OpenAI dashboard. | Receipt system records every action with full governance trace. Searchable in War Room. |
| **Tool Security** | Developer-defined tools with no sandboxing framework. | Capability-based access with default-deny, Docker sandboxing, command denylist, workspace boundary enforcement. |
| **Recovery** | No built-in rollback. Developer must implement recovery logic. | Automatic rollback for T1 actions on verification failure. Commit-based memory rollback. |

**Choose OpenAI Agents SDK if:** You want the fastest path to a working agent with OpenAI models and don't need enforcement-level governance.

**Choose Lancelot if:** You need constitutional constraints enforced by code, not prompts.

---

## LangGraph

**What it is:** LangChain's framework for building stateful, multi-step agent workflows as graphs.

**What it's good at:** Complex workflow orchestration, state management across steps, visualization of agent graphs, large ecosystem of integrations.

**How it differs from Lancelot:**

| Dimension | LangGraph | Lancelot |
|-----------|-----------|----------|
| **Governance** | No built-in governance layer. Developers can add conditional nodes for safety checks. | Constitutional governance (Soul) + Policy Engine + Risk Tiers. Governance is the architecture, not an add-on. |
| **Verification** | Developers can add verification nodes to the graph. Not built-in. | Built-in Verifier agent in the Plan-Execute-Verify loop. Verification is mandatory, not optional. |
| **Memory** | State management via checkpoints. No quarantine or governed edits. | Tiered memory with quarantine, commit-based edits, and rollback. |
| **Observability** | LangSmith integration for tracing. | Receipt system with governance traces, risk tier decisions, and approval records. |
| **Extensibility** | Extensive integrations via LangChain ecosystem. | Governed connector and skill system with manifest-declared permissions. |

**Where LangGraph is stronger:** Ecosystem size, community, integrations, workflow visualization, flexibility in agent design patterns.

**Choose LangGraph if:** You need maximum flexibility in agent design and access to the LangChain ecosystem.

**Choose Lancelot if:** You need governance guarantees enforced at the system level, not added as workflow nodes.

---

## CrewAI

**What it is:** Framework for building collaborative multi-agent teams with role-based task delegation.

**What it's good at:** Multi-agent collaboration, role specialization, task decomposition, accessible API.

**How it differs from Lancelot:**

| Dimension | CrewAI | Lancelot |
|-----------|--------|----------|
| **Governance** | Role-based constraints via agent definitions. No constitutional enforcement. | Constitutional Soul document enforced by code. Role is governance, not suggestion. |
| **Verification** | Inter-agent review possible but not enforced. | Mandatory verification in the Plan-Execute-Verify pipeline. |
| **Memory** | Shared memory between agents. No quarantine or governance on memory writes. | Governed memory with quarantine, commit-based edits, and rollback. Memory writes are T1 actions with verification. |
| **Tool Security** | Agent-level tool assignment. No sandboxing framework. | Capability-based tool access with Docker sandboxing, policy engine, and risk classification. |

**Where CrewAI is stronger:** Multi-agent collaboration patterns, role specialization, faster to prototype collaborative workflows.

**Choose CrewAI if:** You want multi-agent teams collaborating on tasks with role-based specialization.

**Choose Lancelot if:** You need a single, deeply governed agent with constitutional constraints rather than a team of loosely constrained agents.

---

## AutoGen (Microsoft)

**What it is:** Microsoft's framework for building multi-agent conversational systems.

**What it's good at:** Conversational multi-agent patterns, human-in-the-loop integration, code execution, research-oriented architecture.

**How it differs from Lancelot:**

| Dimension | AutoGen | Lancelot |
|-----------|---------|----------|
| **Governance** | Human-in-the-loop for approval. No constitutional governance. | Constitutional Soul + Policy Engine + Risk Tiers. Human-in-the-loop at T3, but T0-T2 are governance-automated. |
| **Memory** | Teachable agents with memory injection. No quarantine or governed edits. | Tiered memory with quarantine, commit-based edits, and full rollback capability. |
| **Observability** | Conversation logging. | Full receipt system with governance traces and risk tier decisions. |
| **Tool Security** | Docker-based code execution available. No capability-based access control. | Capability-based access with default-deny, policy engine, workspace boundary enforcement. |

**Where AutoGen is stronger:** Multi-agent conversational patterns, academic/research use cases, Microsoft ecosystem integration.

**Choose AutoGen if:** You want multi-agent conversations with human-in-the-loop and are in the Microsoft ecosystem.

**Choose Lancelot if:** You need proportional governance (not just human-in-the-loop for everything) with constitutional enforcement.

---

## Devin / Codegen Agents

**What they are:** Autonomous coding agents (Devin by Cognition, plus similar tools like Cursor Agent, Windsurf, etc.).

**What they're good at:** Autonomous software development, code generation, PR creation, issue resolution.

**How they differ from Lancelot:**

| Dimension | Codegen Agents | Lancelot |
|-----------|---------------|----------|
| **Scope** | Specialized for software development. | General-purpose governed autonomous system. Software development is one capability, not the only one. |
| **Governance** | Typically minimal — sandbox + output review. | Constitutional governance across all action types, not just code. |
| **Verification** | Code-level checks (tests, linting). | System-level verification of all action outcomes via Verifier agent. |
| **Observability** | Session replay, diff review. | Full governance trace for every action via receipt system. |

**Where codegen agents are stronger:** Deep IDE integration, specialized for development workflows, faster for pure coding tasks.

**Choose a codegen agent if:** Your primary use case is autonomous software development.

**Choose Lancelot if:** You need a governed autonomous system that can do many things (not just code), with constitutional constraints on all of them.

---

## The Governance Gap

The common thread across all comparisons: **most frameworks leave governance as the developer's problem.** They provide tools for building agents but not for constraining them.

Lancelot's position is that governance is not an add-on — it is the architecture. The Soul, Policy Engine, Risk Tiers, Trust Ledger, and APL exist because an autonomous system without constitutional control is a liability, not a product.

This means Lancelot is:
- **Slower to start with** — there's more to configure (Soul, governance YAML, trust thresholds)
- **More constrained** — the system will actively prevent actions that violate governance
- **More auditable** — every action has a receipt with a full governance trace
- **More recoverable** — rollback, quarantine, and kill switches are built in

Whether this tradeoff makes sense depends entirely on your use case.

---

## Decision Guide

**Choose Lancelot if:**
- You need an AI that can act autonomously but under explicit constitutional control
- You're security-conscious and want enforcement-level governance, not prompt-level suggestions
- You need full auditability of everything the agent does
- You want proportional governance (low-risk actions are fast, high-risk actions require approval)
- You prefer self-hosted, local-first deployment
- You're building for trust, not just capability

**Choose a framework (LangGraph, CrewAI, AutoGen, etc.) if:**
- You need maximum flexibility in agent design
- You want access to large ecosystems of integrations
- You're prototyping and governance can come later
- You need multi-agent collaboration patterns
- You're building a chatbot or assistant, not an autonomous operator
- You're comfortable implementing your own governance layer

**Choose a codegen agent (Devin, etc.) if:**
- Your primary use case is autonomous software development
- You want deep IDE integration
- Governance requirements are limited to code review
