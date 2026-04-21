# Skills Runbook

## Overview
The Skills subsystem manages modular, permissioned capabilities.

## Feature Flag
`FEATURE_SKILLS=true|false` — disable to boot without skill registry.

## Registry
- Persistence: `data/skills_registry.json`
- Install via `install_skill(manifest_path)`
- Enable/disable via War Room or registry API

## Skill Factory
- Proposals stored in `data/skill_proposals.json`
- Proposal artifacts stored under `data/skill_proposals/<proposal_id>/`
- Each proposal package contains:
  - `skill.yaml`
  - `security_manifest.yaml`
  - `execute.py`
  - generated tests
  - artifact hashes for conformance checks
- Proposals start `pending` only after the shared Skill Security Pipeline passes
- `review_failed` proposals must be regenerated or replaced before operator approval
- Owner must approve before installation
- Installation re-validates the exact reviewed artifact before it reaches the registry

## Marketplace
- Marketplace skills default to restricted permissions
- Only `read_input`, `write_output`, `read_config` allowed by default
- Elevated permissions require explicit owner approval

### Troubleshooting
- **Install fails**: Check runtime manifest, security manifest, and proposal artifact hashes
- **Review failed**: Open the Skills panel and inspect `static_analysis` or `sandbox_test` stage output
- **Skill disabled**: Re-enable via `enable_skill(name)` or War Room
- **Marketplace restricted**: Check verify_marketplace_permissions() output
