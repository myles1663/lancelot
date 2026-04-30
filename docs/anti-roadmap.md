# Anti-Roadmap

## What We Will Not Build — and Why

---

Most projects publish roadmaps — lists of features they plan to add. This is the opposite: a list of things we have explicitly decided **not** to build.

An anti-roadmap exists because saying "no" is the hardest part of building a governed system. Every item on this list has been proposed, considered, and rejected — not because it's impossible, but because it would compromise the principles that make Lancelot a **Governed Autonomous System** rather than another agent framework.

This document signals three things about the project:
1. We know what category we're building in, and we intend to stay in it
2. We have engineering discipline — features that don't fit get cut, not shoe-horned
3. If you're evaluating Lancelot, you can trust that these boundaries are durable

---

Lancelot's non-negotiable principles:

- Governance > convenience
- Verification > speed
- Receipts > "trust me"
- Reversible state > "just run it"

Every item below was rejected because it conflicts with at least one of these principles.

---

## 1. We will not chase consumer assistant features

**Not now:**
- Voice-first assistant UX
- Social companion behavior
- Generic personal assistant flows
- "Magic" personality tuning

**Why:** These features push Lancelot toward a chatbot category where governance feels like friction. Lancelot is an operator system, not a consumer product. The moment governance becomes "annoying" rather than "essential," the architecture is fighting the product, and the product will win. We'd rather stay in a category where governance is valued.

---

## 2. We will not ship unconstrained computer control

**Not now:**
- Fully autonomous GUI driving across arbitrary apps
- Uncontrolled browsing or clicking modes
- Direct OS automation without capability scopes

**Why:** This is the fastest route to catastrophic trust failure. An agent that can click anything on your screen is an agent that can do anything to your system. We will not ship capabilities that cannot be governed, scoped, and revoked. When we add computer control, it will be through the capability system with explicit governance — not as "let the model drive."

---

## 3. We will not become just a framework

**Not now:**
- Turning Lancelot into a generic agent SDK
- Supporting every orchestration style
- Prioritizing library ergonomics over system integrity

**Why:** Frameworks optimize for flexibility; systems optimize for guarantees. If Lancelot becomes "a library for building agents," governance becomes optional, receipts become opt-in, and the Soul becomes a suggestion. The entire security model depends on Lancelot being a **system** with enforced invariants, not a toolkit where developers can skip the parts they find inconvenient.

---

## 4. We will not enable uncontrolled third-party skills

**Not now:**
- Install-any-skill marketplaces
- Self-replicating skill factories
- Marketplace skills with default exec/network/file access

**Why:** Skills are supply chain risk. Every third-party skill is a potential vector for data exfiltration, privilege escalation, or behavioral manipulation. Marketplace skills are restricted to `read_input`, `write_output`, and `read_config` permissions. Elevated permissions require explicit owner approval. We will not build an app store that makes it easy to install ungoverned code.

---

## 5. We will not rely on RAG vibes for correctness

**Not now:**
- Retrieval as the primary source of truth
- Vector search deciding system state

**Why:** Retrieval is lossy. Embedding similarity is not semantic understanding. When an agent's behavior depends on which chunks a vector search happened to return, correctness becomes probabilistic. Lancelot uses deterministic context loading — structured memory tiers compiled into a token-budgeted context window. You know exactly what the model sees, and it's the same every time.

---

## 6. We will not trade verification for speed

**Not now:**
- Skipping verifier steps for faster execution
- Auto-executing high-risk actions based on confidence alone

**Why:** Speed without verification collapses trust. The Planner/Executor/Verifier loop exists because autonomous actions must be checked, not assumed correct. A model that says "95% confidence" is not 95% reliable — it's a model that *thinks* it's confident, which is a very different thing. Verification is the price of autonomy. We will not discount it.

---

## 7. We will not broaden autonomy without stronger controls

**Not now:**
- Expanding Crusader mode into irreversible actions
- Background jobs mutating systems without approval

**Why:** Autonomy must remain bounded. Crusader mode (high-agency execution) explicitly does not override Soul constraints, approval requirements, or risk rules. We will expand autonomy only when governance can keep pace — which means trust graduation, APL (approval pattern learning), and tier boundary enforcement must be robust before autonomy grows.

---

## 8. We will not add multi-tenant enterprise features yet

**Not now:**
- Organization-level RBAC
- Multi-tenant SaaS architecture

**Why:** Multi-tenancy multiplies risk and slows iteration. Lancelot's governance model is built on single-owner allegiance — one Soul, one owner, one trust boundary. Adding multi-tenant support means solving authorization, data isolation, cross-tenant governance, and role hierarchy. These are solvable problems, but they're not *our* problem right now. Single-owner allegiance is a feature, not a limitation.

---

## 9. We will not add integration sprawl

**Not now:**
- Dozens of SaaS connectors
- Shallow integrations with high maintenance cost

**Why:** Integrations become long-term debt. Every connector is a credential to manage, an API to keep compatible, a surface area to secure. We build deep, governed connectors for specific use cases rather than shallow connectors for everything. Quality over quantity. When we add a connector, it goes through the full governance pipeline — manifests, risk tiers, trust scoring, receipts.

---

## What We Build Instead

Every feature we ship must improve at least one of:

1. **Governance** — Stronger constraints, better enforcement
2. **Trust** — More observable, more auditable, more recoverable
3. **Deterministic context** — Better memory, better context compilation
4. **Operational safety** — Fewer failure modes, safer defaults
5. **Operator UX** — Better War Room, better receipts, better visibility

If a proposed feature doesn't improve any of these, it doesn't ship.

---

## Decision Test

Before building anything new, we ask:

1. Does this increase trust more than risk?
2. Is it reversible?
3. Is it fully receipt-traced?
4. Does it fit the GAS category?

If the answer to any of these is "no," we do not build it.

---

*This anti-roadmap is a living document. Items may be reconsidered as the governance model matures and new control mechanisms prove themselves. But the principles behind the decisions — governance, verification, receipts, reversibility — are permanent.*
