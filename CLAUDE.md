# CLAUDE.md — Lancelot Dev Workflow & Memory

## Repo Structure
This is the PRIVATE development repo for Lancelot.
- `origin` → `https://github.com/myles1663/lancelot-dev.git` (private, default push)
- `public` → `https://github.com/myles1663/lancelot.git` (public, release target)

Never push directly to `public` without explicit instruction from Myles.

The fuller SOP lives at `C:\Users\SSAdministrator\Desktop\Lancelot_Git_SOP.md`.

---

## Day-to-Day Dev Workflow
All development happens on this private repo. Commit freely and often.
Push to origin (private) after every meaningful working state:
```bash
git add <specific files>
git commit -m "descriptive message"
git push origin master
```

Rules:
- Stage specific files only. Do not use `git add -A` or `git add .`.
- Never push directly to `public` unless Myles explicitly instructs a release.
- Never commit `.env`, credentials, secrets, or unnecessary large generated artifacts.

---

## Documentation Requirements
Any time a new feature, subsystem, architecture change, or behavioral
change is implemented, the following documentation must be updated
before the work is considered complete. Do not mark a task done until
all affected docs are current.

Files to audit and update on every meaningful change:

- `README.md` — feature list, architecture overview, any changed
  install or usage instructions
- `CHANGELOG.md` — new entry under [Unreleased] describing what changed
- `docs/architecture.md` — if subsystem relationships, data flows,
  or component responsibilities changed
- `docs/soul-spec.md` — if Soul Engine behavior, constraints, or
  directives changed
- `docs/connectors.md` — if any connector was added, removed, or modified
- `docs/kill-switches.md` — if any feature flag was added, removed,
  or had its dependencies changed
- `docs/trust-ledger.md` — if graduation thresholds, revocation logic,
  or tier behavior changed
- `docs/apl.md` — if Approval Pattern Learning rules or behavior changed
- `docs/receipt-system.md` — if receipt schema, generation logic,
  or storage changed
- `docs/skill-security.md` — if any pipeline stage changed
- `docs/hive.md` — if Agent Mesh topology, task decomposition,
  or Soul inheritance changed
- `docs/uab.md` — if Universal App Bridge hooks, supported apps,
  or programmatic control changed
- `docs/compliance-export.md` — if compliance export formats, control
  mappings, chain integrity, or export pipeline changed
- `docs/observability.md` — if OTel metrics, webhook categories, Metrics
  API endpoints, or dashboard templates changed
- `docs/time-travel.md` — if time-travel modes, fork pipeline, Soul
  fork_permissions schema, or state snapshot behavior changed
- `docs/a2a.md` — if A2A protocol support, inbound/outbound pipelines,
  agent card, registry, or Soul a2a_permissions changed
- `docs/soul-templates.md` — if Soul Template Library templates,
  registry, apply flow, or War Room UI changed
- `docs/incident-response.md` — if incident trigger rules, playbook
  definitions, incident lifecycle, report generation, or War Room
  Incidents Dashboard changed
- `FIGURE_REFERENCE_GUIDE.md` — if any War Room panel, UI flow,
  or architecture diagram needs a new or updated figure

If a doc file does not yet exist and the feature warrants it, create it.

### Documentation Update Procedure

When a feature, subsystem, or behavioral change is complete, run this
procedure before marking the work done:

1. **Identify scope** — List every subsystem touched (new modules,
   modified modules, new feature flags, new receipt types, new API
   endpoints, new UI pages/components).

2. **Audit the checklist above** — For each file in the list, ask:
   "Did this change affect what this doc describes?" If yes, update it.
   If the doc doesn't exist yet, create it.

3. **CHANGELOG.md** — Always add an entry under `[Unreleased]`.
   Group by: `Added`, `Changed`, `Fixed`, `Removed`. Be specific —
   include module paths, class names, feature flag names, and endpoint
   routes. This is the single source of truth for what changed between
   releases.

4. **README.md** — Update the Key Capabilities table if a new
   subsystem was added. Update the Documentation table if a new doc
   was created. Update the Project Structure tree if new top-level
   source directories were added.

5. **docs/architecture.md** — Add a subsystem section if a new
   subsystem was created. Update the Subsystem Independence table.
   Update the Security Architecture section if new security boundaries
   were added.

6. **docs/security.md** — Add a security section for any new
   subsystem that introduces external communication, credential
   handling, new attack surfaces, or new governance gates.

7. **docs/governance.md** — Update if the governance model was
   extended (new gates, new Soul fields, new permission models).

8. **docs/receipts.md** — Update the "What Generates Receipts" table
   and action_type field reference if new receipt types were added.

9. **Subsystem-specific docs** — Update or create the dedicated doc
   for any subsystem that was added or significantly modified:
   - `docs/federation.md` — Federation Data Plane
   - `docs/mcp.md` — MCP Governance
   - `docs/hive.md` — Hive Agent Mesh
   - `docs/uab.md` — Universal Application Bridge
   - `docs/connectors.md` — Connector system
   - `docs/kill-switches.md` — Feature flags
   - `docs/trust-ledger.md` — Trust Ledger
   - `docs/apl.md` — Approval Pattern Learning
   - `docs/skill-security.md` — Skill Security Pipeline
   - `docs/incident-response.md` — Incident Response Playbooks

10. **Cross-reference check** — Verify that new docs are linked from
    README.md's Documentation table and from architecture.md's
    subsystem section. Verify that "For the full reference, see [X]"
    links point to the correct files.

**Rule:** Documentation is not a follow-up task. It is part of the
implementation. A feature without updated docs is not done.

---

## Release and Squash Merge Procedure
Only execute this procedure when Myles explicitly says
"ready to release" or "merge to public."

Never initiate a release on your own judgment.

When release is approved:
```bash
# Create a clean release branch from current master
git checkout -b release-prep

# Squash all dev commits into a single clean commit from master
git merge --squash master

# Myles will provide the release commit message
# Wait for it before committing
git commit -m "[RELEASE MESSAGE PROVIDED BY MYLES]"

# Push squashed commit to public main
git push public release-prep:main

# Clean up release branch
git checkout master
git branch -d release-prep
```

After pushing to public, update `CHANGELOG.md` to move all entries
from [Unreleased] into a new versioned section with today's date,
then push that changelog update to origin (private) and public.

---

## Dev Versioning & Rollback
Tag every stable working state in the private repo so we can roll back
if something breaks or we don't like the direction.

**Tag format:** `dev-v{version}.{increment}` (e.g., `dev-v0.3.1.1`, `dev-v0.3.1.2`)
- The `dev-` prefix keeps these distinct from public release tags.
- Increment resets when the public version bumps.

**When to tag:**
- After completing a feature or fix that's confirmed working
- Before starting risky or large changes (safety checkpoint)
- After any `docker compose up -d --build` that passes smoke tests

**How to tag:**
```bash
git tag -a dev-v0.3.1.1 -m "short description of stable state"
git push origin dev-v0.3.1.1
```

**How to roll back:**
```bash
# See available checkpoints
git tag -l "dev-*"

# Roll back to a known good state
git reset --hard dev-v0.3.1.x
git push origin master --force
```

**Rules:**
- Tag after confirmed working states, before risky changes, and after successful rebuild plus smoke test.
- Never delete tags — they're the safety net.
- Tag messages should be brief but descriptive (e.g., "NVIDIA provider + installer credential fix").

---

## General Principles
- The public repo is what acquirers, partners, and the world sees.
  Every public commit should be clean, intentional, and well-described.
- The private repo is the workshop. Messy commits are fine here.
- When in doubt about whether something affects documentation, it does.
  Update the docs.
- Never expose credentials, API keys, or .env contents in any commit
  to either repo.
- BSL 1.0 license headers must be present on all new source files.
  Licensor: Myles Russell Hamilton.
