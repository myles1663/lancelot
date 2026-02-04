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
- Proposals start PENDING — cannot auto-enable
- Owner must approve before installation

## Marketplace
- Marketplace skills default to restricted permissions
- Only `read_input`, `write_output`, `read_config` allowed by default
- Elevated permissions require explicit owner approval

### Troubleshooting
- **Install fails**: Check skill.yaml for valid name, version, permissions
- **Skill disabled**: Re-enable via `enable_skill(name)` or War Room
- **Marketplace restricted**: Check verify_marketplace_permissions() output
