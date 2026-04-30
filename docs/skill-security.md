# Skill Security Pipeline

**Feature Flag:** `FEATURE_SKILL_SECURITY_PIPELINE` (default: `false`, requires `FEATURE_SKILLS`)
**Codebase:** `src/skills/security/pipeline.py`

The Skill Security Pipeline is a 6-stage evaluation process for new skills before installation. It ensures that only validated, owner-approved skills with properly registered capabilities enter the system.

---

## Pipeline Stages

### Stage 1: Manifest Validation

Validates the skill's YAML/JSON manifest structure:

- Required fields present (id, name, version, capabilities)
- Field types correct
- Capability declarations well-formed
- Returns audit summary if passed

### Stage 2: Static Analysis

Scans skill source files for security issues:

- Hardcoded credentials
- Dangerous imports (os.system, subprocess, eval)
- Unsafe patterns
- Returns: critical count, warning count, files scanned
- **Fails if any critical violation found**

### Stage 3: Sandbox Testing

Runs skill operations in an isolated sandbox:

- Filesystem escape attempts detected
- Unallowed network access blocked
- Process spawning monitored
- Tests each declared operation
- Returns: violations list, operations tested

### Stage 4: Owner Review (External)

Pipeline returns Stages 1–3 results for owner review. The owner examines the audit summary, static analysis findings, and sandbox test results, then approves or rejects.

### Stage 5: Capability Registration

After owner approval:

- Registers skill in the `CapabilityEnforcer`
- Installs approved capabilities into the skill registry
- Adds skill to the factory for future instantiation

### Stage 6: Trust Initialization

If the Trust Ledger is available:

- Creates trust records for each approved capability
- Initializes each at T2 (Controlled)
- Enables future graduation based on execution success

---

## Pipeline Result

```python
PipelineResult:
    skill_id: str
    passed: bool
    stage_results: Dict[str, Any]
    failed_at_stage: str        # Stage name if failed
    approved_capabilities: List[str]
    manifest: Optional[SkillManifest]
```

---

## API

- `evaluate(skill_path, manifest_dict) → PipelineResult` — Stages 1–3, returns for review. Does not modify system state.
- `approve_and_install(pipeline_result, approved_capabilities) → bool` — Stages 5–6 after owner approval.
- `uninstall(skill_id) → None` — Remove from enforcer.

---

## Skill Ownership Model

| Source | Trust Level | Execution |
|--------|-------------|-----------|
| **SYSTEM** | Built-in, full trust | In-process |
| **USER** | Owner-installed | Sandboxed Docker container |
| **MARKETPLACE** | Third-party, restricted | Sandboxed, limited to read_input/write_output/read_config |

Non-builtin skills execute in isolated Docker containers: read-only mount, `--network=none`, 256MB memory, 1 CPU, 60-second timeout.
