# Runbook: Risk-Tiered Governance

## Checking Governance Status

### War Room Panel
Navigate to the War Room's "Governance Performance" tab to see:
- Policy cache hit rate and entry count
- Async verification queue depth
- Intent template status (active/candidate counts)
- Feature flag status

### Via Gateway API
```bash
curl http://localhost:8000/governance/stats
```

## Toggling Feature Flags

Feature flags are environment variables set in `docker-compose.yml` or `.env`:

```yaml
# Enable the full governance system
FEATURE_RISK_TIERED_GOVERNANCE=true
FEATURE_POLICY_CACHE=true
FEATURE_ASYNC_VERIFICATION=true
FEATURE_INTENT_TEMPLATES=true
FEATURE_BATCH_RECEIPTS=true
```

After changing, rebuild the container:
```bash
docker compose up -d --build
```

## Invalidating the Policy Cache

The policy cache auto-invalidates on Soul changes. To manually invalidate:

1. Via the Gateway API (when endpoint is available):
   ```bash
   curl -X POST http://localhost:8000/governance/cache/invalidate
   ```

2. Via container restart — cache recompiles on boot.

3. Via Soul reload — any Soul amendment triggers cache invalidation.

## Inspecting Intent Templates

Templates are stored in `lancelot_data/governance/intent_templates.json`.

To view all templates:
```bash
docker exec lancelot_core cat /home/lancelot/data/governance/intent_templates.json | python -m json.tool
```

To invalidate all templates (e.g., after Soul change):
This happens automatically when the Soul is amended. The registry calls `invalidate_all()`.

## Investigating a Verification Failure

1. Check the container logs:
   ```bash
   docker logs lancelot_core 2>&1 | grep "verification"
   ```

2. Look for rollback entries:
   ```bash
   docker logs lancelot_core 2>&1 | grep "Rolled back"
   ```

3. Check the async queue results in the War Room panel.

4. Verification failures in T1 actions trigger automatic rollback — the file is restored to its pre-execution state.

## Reviewing Batch Receipts

Batch receipts are JSON files in `lancelot_data/governance/`:

```bash
docker exec lancelot_core ls /home/lancelot/data/governance/batch_*.json
docker exec lancelot_core cat /home/lancelot/data/governance/batch_*.json | python -m json.tool
```

Each receipt contains:
- `entries[]`: Individual action records with SHA-256 hashes
- `summary`: Total actions, succeeded, failed, highest risk tier

## Handling a Rollback

Rollbacks are automatic for T1 verification failures:
- **fs.write**: Original file content is restored (or new file is deleted)
- **git.commit**: Rollback strategy notes `git revert`
- **memory.write**: Rollback strategy notes CommitManager revert

If a rollback fails, it's logged as an error. Check:
```bash
docker logs lancelot_core 2>&1 | grep "Rollback failed"
```

## Troubleshooting

### "Why is my action being blocked?"

1. **Check the risk tier**: The action may be classified higher than expected.
   - Scope escalation: `fs.write` outside workspace → T3
   - Pattern escalation: `*.env` files → T3
   - Soul escalation: Soul contract overrides

2. **Check policy cache**: A cached "deny" decision may be blocking it.

3. **Check prior verification failures**: T2/T3 actions are blocked if prior T1 verifications failed.

### "Why isn't the cache being used?"

1. **Feature flag off**: `FEATURE_POLICY_CACHE` must be `true`
2. **Soul version mismatch**: If `validate_soul_version=true`, cache entries are rejected when Soul version changes
3. **T2/T3 action**: Cache only contains T0/T1 entries — T2/T3 always go through full evaluation

### "Why isn't my template being used?"

1. **Not promoted**: Templates need `promotion_threshold` (default: 3) successful executions
2. **Invalidated**: Soul changes invalidate all templates
3. **Deactivated**: Too many failures (failure_count > success_count)
4. **Flag off**: `FEATURE_INTENT_TEMPLATES` must be `true`

## Emergency: Disable Risk-Tiered Governance

To immediately disable the entire governance system:

```bash
# Set the master switch to false
# In docker-compose.yml environment section:
FEATURE_RISK_TIERED_GOVERNANCE=false
```

Then rebuild:
```bash
docker compose up -d --build
```

This reverts `execute_plan()` to the legacy synchronous path with zero behavioral change. All other subsystems continue to function normally.
