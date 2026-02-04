# Soul Runbook

## Overview
The Soul subsystem manages Lancelot's constitutional identity.

## Feature Flag
`FEATURE_SOUL=true|false` — disable to boot without soul loading/linting.

## Common Operations

### Check Active Version
```
GET /soul/status
```

### Propose Amendment
1. Create proposal via `create_proposal(from_version, yaml_text)`
2. Review diff in War Room Soul panel
3. Approve via `POST /soul/proposals/{id}/approve`
4. Activate via `POST /soul/proposals/{id}/activate`

### Troubleshooting
- **Lint failure on load**: Check soul.yaml against invariant rules
- **Version file not found**: Verify soul/soul_versions/ contains matching file
- **ACTIVE pointer stale**: Check soul/ACTIVE contents
