# Contributing to Lancelot

Thank you for your interest in contributing to Lancelot. This guide covers how to set up a development environment, run tests, submit contributions, and what kinds of changes are welcome.

Lancelot is a Governed Autonomous System. Contributions must preserve that category.

---

## Non-Negotiables

These are the project's hard constraints. PRs that weaken any of these properties will not be merged:

1. **All actions must be receipt-traced** — If your code performs an action, it must emit a receipt
2. **New autonomy requires governance gates** — New autonomous capabilities must be gated by the Policy Engine
3. **Memory must remain reversible** — Memory edits must be commit-based and rollback-safe
4. **Security guarantees come first** — Security is never traded for features or convenience

Read the [Anti-Roadmap](docs/anti-roadmap.md) before proposing new features. Contributions that violate anti-roadmap principles will not be accepted.

---

## Development Setup

### Prerequisites

- Python 3.11+
- Docker Desktop (for running the full system)
- Git

### Local Development (without Docker)

```bash
git clone https://github.com/myles1663/lancelot.git
cd lancelot
pip install -r requirements.txt
```

### Running the Test Suite

```bash
# Run all tests
pytest tests/ -x

# Run with timeout enforcement
pytest tests/ -x --timeout=30

# Run only unit tests (no external services)
pytest tests/ -x -m "not integration and not slow and not docker and not local_model"

# Run with coverage
pytest tests/ --cov=src --cov-report=term-missing
```

**Test markers:**
- `integration` — Requires external services (LLM APIs, Docker)
- `slow` — Takes more than a few seconds
- `docker` — Requires Docker runtime
- `local_model` — Requires the local GGUF model

### Running Inside Docker

```bash
docker compose up -d --build
MSYS_NO_PATHCONV=1 docker exec lancelot_core pytest tests/ -x
```

Note: On Windows with Git Bash, prefix Docker commands with `MSYS_NO_PATHCONV=1` to prevent path mangling.

---

## Code Style and Conventions

- **Python 3.11+** — Use modern Python features (type hints, dataclasses, Pydantic v2)
- **Pydantic for data models** — All schemas and data structures use Pydantic
- **PyYAML for configuration** — All config files are YAML
- **SQLite for persistence** — Scheduler, memory, and indexed data use SQLite
- **JSON for registries** — Skill registry, proposals, and receipts use JSON

### Naming

- Snake case for Python files, functions, and variables
- PascalCase for classes
- Feature flags: `FEATURE_` prefix, uppercase with underscores

### Testing Standards

- **Unit tests:** Deterministic, no network calls, injected timers
- **Integration tests:** Marked with `@pytest.mark.integration`, require real services
- **Test helpers:** Use `_minimal_soul_dict()`, `_write_config()`, `_write_sched_config()` from test fixtures
- **Cleanup:** Feature flag tests must call `reload_flags()` in teardown

---

## Branching Model

Trunk-based development with short-lived branches:

```
main = stable, releasable, protected (no direct commits)

Branch naming:
  feat/<slug>    — New features
  fix/<slug>     — Bug fixes
  chore/<slug>   — Maintenance tasks
  docs/<slug>    — Documentation changes
```

One branch = one logical change. Keep branches small and focused.

---

## PR Process

### Before Submitting

- [ ] Code follows existing patterns and conventions
- [ ] All new actions emit receipts
- [ ] New features are gated by feature flags (kill switches)
- [ ] Unit and integration tests are added
- [ ] Existing tests still pass (`pytest tests/ -x`)
- [ ] CHANGELOG.md is updated
- [ ] Relevant documentation is updated

### PR Checklist

For substantial features (new subsystems, new add-ons):

- [ ] Specification created (new doc, not modified active spec)
- [ ] Blueprint created
- [ ] Feature flag added (kill switch)
- [ ] Contracts/interfaces defined
- [ ] Receipts emitted for all new actions
- [ ] War Room visibility added (if applicable)
- [ ] Runbook added or updated
- [ ] CHANGELOG updated

### Review Process

All PRs are reviewed against:
1. Does it preserve the GAS category?
2. Are all actions receipt-traced?
3. Is the feature gated (kill-switchable)?
4. Does it weaken any security guarantees?
5. Does it violate the anti-roadmap?

---

## What Contributions Are Welcome

### Always Welcome

- **Bug fixes** — with tests that reproduce the bug
- **Test coverage** — new tests for existing code
- **Documentation improvements** — corrections, clarifications, examples
- **Performance improvements** — that don't weaken governance
- **Security hardening** — additional defenses, vulnerability fixes

### Welcome with Discussion First

- **New connectors** — must go through the full governance pipeline (manifest, vault, risk tiers)
- **New skills** — must pass the security pipeline with honest permission declarations
- **Subsystem improvements** — should align with existing architecture patterns
- **War Room enhancements** — new panels or improvements to existing panels

### Requires a Spec

- **New subsystems** — any new background process or runtime dependency needs its own spec and blueprint
- **Governance changes** — modifications to the risk tier model, policy engine, or Soul linter
- **Memory architecture** — changes to the tiered memory model or context compiler

### Will Not Be Accepted

- Features that weaken governance (optional governance, skippable verification)
- Consumer chatbot features (voice assistant, personality tuning)
- Unconstrained computer control (autonomous GUI driving without capability scopes)
- Generic framework behavior (turning Lancelot into an SDK)
- Uncontrolled skill marketplaces
- RAG as a primary truth source replacing structured memory
- Multi-tenant enterprise features (at this stage)

See the [Anti-Roadmap](docs/anti-roadmap.md) for the full list and reasoning.

---

## Code of Conduct

Be respectful, constructive, and direct. We're building something that needs to be trustworthy — that standard applies to how we work together, not just to the code.

- Disagreements about technical approach are welcome
- Explain your reasoning — "because governance" is not enough; explain the specific mechanism
- If you're unsure whether a contribution fits, open an issue to discuss before writing code

---

## Getting Help

- **Issues:** [github.com/myles1663/lancelot/issues](https://github.com/myles1663/lancelot/issues)
- **Documentation:** Start with the [Architecture](docs/architecture.md) doc to understand the system
- **Configuration:** See the [Configuration Reference](docs/configuration-reference.md)
