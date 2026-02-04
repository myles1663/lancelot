# Lancelot Changelog

## v4.0.0 — Multi-Provider Upgrade (2026-02-03)

**Spec:** [docs/specs/Lancelot_v4Next_Spec_MultiProvider_Upgrade.md](specs/Lancelot_v4Next_Spec_MultiProvider_Upgrade.md)
**Blueprint:** [docs/blueprints/Lancelot_v4Next_Blueprint_MultiProvider_Upgrade.md](../docs/blueprints/Lancelot_v4Next_Blueprint_MultiProvider_Upgrade.md)

### Summary

Complete v4 upgrade transforming Lancelot from a Gemini-only system into a
multi-provider AI platform with mandatory local utility models, unbrickable
onboarding, and a War Room control plane.

### Phases Completed

#### Phase 1 — Unbrickable Onboarding (Prompts 0-7)
- Test harness baseline with pytest markers and conftest
- 11-state OnboardingSnapshot with atomic disk persistence
- Recovery commands: STATUS, BACK, RESTART STEP, RESEND CODE, RESET ONBOARDING
- COOLDOWN state replacing legacy LOCKDOWN with exponential backoff
- Control-plane API endpoints mounted as FastAPI APIRouter
- War Room recovery panel in Streamlit UI

#### Phase 2 — Local Model Package (Prompts 8-12)
- Local model scaffold: lockfile, fetch, smoke test modules
- models.lock.yaml with Hermes 2 Pro Mistral 7B Q4_K_M (Apache-2.0)
- local-llm Docker service with FastAPI /health and /v1/completions
- Mandatory LOCAL_UTILITY_SETUP onboarding state

#### Phase 3 — Model Router & Provider Lanes (Prompts 13-16)
- LocalModelClient HTTP client with 5 utility methods
- Runtime models.yaml and router.yaml with ProfileRegistry
- ModelRouter v1 with local utility routing and receipt logging
- ModelRouter v2 with fast/deep flagship lanes and escalation
- FlagshipClient: provider-agnostic HTTP client for Gemini, OpenAI, Anthropic
- Risk-based escalation: task types, keywords, failure retry (fast to deep)

#### Phase 4 — Cost Telemetry & Hardening (Prompts 17-18)
- UsageTracker with per-lane cost estimation and local savings calculation
- /usage/summary, /usage/lanes, /usage/savings, /usage/reset endpoints
- Error leakage prevention across all API endpoints
- Health check hardening with try/except safety
- Download timeout protection for model fetching
- Inference error sanitisation in local-llm server
- 26 regression tests covering all hardening fixes

### Test Suite

721 passed, 18 skipped, 0 failures across 18 v4 test files.

### New Files

- `config/models.yaml` — Lane-based model configuration
- `config/router.yaml` — Routing order and escalation config
- `local_models/` — Docker service, lockfile, fetch, smoke test, prompts
- `src/core/onboarding_snapshot.py` — Disk-backed state persistence
- `src/core/recovery_commands.py` — Recovery command handlers
- `src/core/control_plane.py` — War Room API endpoints
- `src/core/local_utility_setup.py` — Onboarding setup orchestration
- `src/core/local_model_client.py` — Local model HTTP client
- `src/core/provider_profile.py` — Typed config loader and registry
- `src/core/model_router.py` — Lane-based routing with receipts
- `src/core/flagship_client.py` — Multi-provider flagship client
- `src/core/usage_tracker.py` — Per-lane cost telemetry
- `src/ui/recovery_panel.py` — Streamlit recovery panel
- `pytest.ini` — Test configuration with markers
- `tests/conftest.py` — Shared fixtures
- 18 test files with 721 tests

### Modified Files

- `docker-compose.yml` — Added local-llm service
- `requirements.txt` — Added pyyaml, pytest dependencies
- `src/core/gateway.py` — Error leakage fixes, health check hardening
- `src/integrations/api_discovery.py` — Error sanitisation
- `src/ui/onboarding.py` — LOCAL_UTILITY_SETUP step integration
- `src/ui/war_room.py` — Router and usage panels
