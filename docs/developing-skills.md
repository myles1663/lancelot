# Developing Skills

A guide to building skills that pass Lancelot's security pipeline — from manifest to sandbox testing to installation.

Skills are Lancelot's extensibility mechanism. They are modular capabilities with declarative manifests, explicit permission requirements, and a governed installation pipeline.

---

## Concepts

### What Is a Skill?

A skill is a self-contained capability with:
- A **manifest** (`skill.yaml`) declaring what it does, what it needs, and what risks it carries
- An **execute module** (`execute.py`) containing the implementation
- **Tests** validating the skill's behavior
- A **governance record** tracking ownership, signature state, and permission approvals

### The Skill Lifecycle

```
Define Manifest → Write Execute Module → Submit Proposal → Owner Approves
  → Install → Enable → Execute (governed) → Disable → Uninstall
```

---

## Step 1: Write the Manifest

Every skill starts with a `skill.yaml` manifest:

```yaml
name: url_summarizer
version: "1.0.0"
description: "Fetches a URL and returns a summarized version of its content"

inputs:
  - name: url
    type: string
    required: true
    description: "The URL to fetch and summarize"
  - name: max_length
    type: integer
    required: false
    description: "Maximum summary length in words (default: 200)"

outputs:
  - name: summary
    type: string
    description: "The summarized content"
  - name: source_url
    type: string
    description: "The original URL"

risk: MEDIUM

permissions:
  - read_input
  - write_output
  - network_fetch

required_brain: flagship_fast

scheduler_eligible: true

receipts:
  emit_on_success: true
  emit_on_failure: true
  include_inputs: true
  include_outputs: true
```

### Manifest Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Lowercase alphanumeric + underscores. Must be unique. |
| `version` | Yes | Semantic version string |
| `description` | Yes | What the skill does (keep it honest) |
| `inputs` | Yes | List of `SkillInput` declarations |
| `outputs` | Yes | List of `SkillOutput` declarations |
| `risk` | Yes | `LOW`, `MEDIUM`, or `HIGH` |
| `permissions` | Yes | List of required permissions |
| `required_brain` | No | Which LLM lane this skill needs (`local_utility`, `flagship_fast`, `flagship_deep`) |
| `scheduler_eligible` | No | Can this skill be invoked by the Scheduler? (default: false) |
| `receipts` | No | Receipt emission configuration |

### Risk Levels

| Level | Meaning | Governance |
|-------|---------|------------|
| `LOW` | Read-only, no side effects | Minimal governance |
| `MEDIUM` | Writes data, reversible side effects | Standard governance, receipted |
| `HIGH` | External actions, potentially irreversible | Full governance, may require approval |

### Permissions

Declare every permission your skill needs. The security pipeline validates these declarations — underdeclaring will get your skill blocked at runtime; overdeclaring reduces trust.

**Marketplace-allowed permissions** (no special approval needed):
- `read_input` — Read the input parameters
- `write_output` — Write the output results
- `read_config` — Read configuration files

**Elevated permissions** (require explicit owner approval):
- `network_fetch` — Make outbound HTTP GET requests
- `network_post` — Make outbound HTTP POST requests
- `file_read` — Read files from workspace
- `file_write` — Write files to workspace
- `shell_exec` — Execute shell commands
- `memory_read` — Read from memory tiers
- `memory_write` — Write to memory tiers

---

## Step 2: Write the Execute Module

Create `execute.py` with your skill implementation:

```python
def execute(inputs: dict) -> dict:
    """
    Main entry point. Receives validated inputs, returns outputs.

    Args:
        inputs: Dictionary matching the manifest's input declarations

    Returns:
        Dictionary matching the manifest's output declarations
    """
    url = inputs["url"]
    max_length = inputs.get("max_length", 200)

    # Your implementation here
    # Note: network calls go through the governed pipeline,
    # not through direct HTTP libraries

    return {
        "summary": "The summarized content...",
        "source_url": url
    }
```

### Key Rules

1. **Accept `inputs` dict, return outputs dict.** The schema must match your manifest.
2. **Don't import ungoverned libraries for network/file access.** Use the capabilities provided by the runtime.
3. **Handle errors gracefully.** Raise exceptions for unrecoverable failures — the executor catches them and generates failure receipts.
4. **Be deterministic where possible.** Given the same inputs, produce the same outputs. This helps verification and trust scoring.

---

## Step 3: Write Tests

Create test files that validate your skill:

```python
def test_basic_summarization():
    result = execute({
        "url": "https://example.com/article",
        "max_length": 100
    })
    assert "summary" in result
    assert "source_url" in result
    assert result["source_url"] == "https://example.com/article"

def test_missing_url():
    try:
        execute({})
        assert False, "Should have raised an error"
    except (KeyError, ValueError):
        pass  # Expected

def test_default_max_length():
    result = execute({"url": "https://example.com/article"})
    assert "summary" in result
```

---

## Step 4: Submit a Proposal

Skills go through a proposal pipeline before installation. Use the Skill Factory:

```python
from skills.factory import SkillFactory

factory = SkillFactory(data_dir="data")

proposal = factory.generate_skeleton(
    name="url_summarizer",
    description="Fetches a URL and returns a summarized version",
    permissions=["read_input", "write_output", "network_fetch"]
)
```

This creates a `SkillProposal` in `PENDING` status, stored in `data/skill_proposals.json`.

**Proposal states:**

```
PENDING → Owner reviews → APPROVED → Installed → INSTALLED
                        → REJECTED (terminal)
```

---

## Step 5: Owner Approval

The owner reviews the proposal in the War Room Skills panel (or via API):

- Views the manifest, permissions requested, and source code
- Checks for elevated permissions that need explicit approval
- Approves or rejects

```python
factory.approve_proposal(proposal_id)
```

### Marketplace Restrictions

If the skill is sourced from a marketplace (`source: marketplace`), additional restrictions apply:

- Only `read_input`, `write_output`, `read_config` permissions are allowed by default
- Elevated permissions trigger a secondary approval step
- The `verify_marketplace_permissions()` function checks compliance

---

## Step 6: Installation

After approval, install the skill:

```python
factory.install_proposal(proposal_id)
```

This:
1. Writes the skill files to the skill directory
2. Registers the skill in the Skill Registry (`data/skills_registry.json`)
3. Sets the skill to enabled status
4. Emits an installation receipt

### Skill Registry Entry

Each installed skill is tracked in the registry:

```json
{
  "name": "url_summarizer",
  "version": "1.0.0",
  "status": "enabled",
  "ownership": "USER",
  "signature": "UNSIGNED",
  "installed_at": "2026-02-14T10:00:00Z",
  "permissions": ["read_input", "write_output", "network_fetch"]
}
```

**Ownership types:**
- `SYSTEM` — Built-in skills (command_runner, repo_writer, network_client, service_runner)
- `USER` — Installed by the owner
- `MARKETPLACE` — Third-party, restricted permissions

**Signature states:**
- `UNSIGNED` — No signature verification
- `SIGNED` — Signed by author
- `VERIFIED` — Signature verified against trusted keys

---

## Step 7: Execution

Once installed and enabled, skills execute through the governed pipeline:

```
Skill invocation
  → SkillExecutor loads module
  → Validates inputs against manifest
  → Checks permissions against registry
  → Executes in sandboxed runtime
  → Validates outputs against manifest
  → Emits receipt
  → Returns result
```

### Runtime Capability Enforcement

If your skill tries to do something not declared in its permissions:
- **Network call without `network_fetch`** → Blocked, failure receipt
- **File write without `file_write`** → Blocked, failure receipt
- **Shell exec without `shell_exec`** → Blocked, failure receipt

The enforcement happens at the runtime level, not just at the manifest level. Undeclared capabilities are blocked regardless of what the code tries to do.

---

## Skill Management

### Enable/Disable

```python
registry = SkillRegistry(data_dir="data")
registry.disable_skill("url_summarizer")   # Temporarily disable
registry.enable_skill("url_summarizer")    # Re-enable
```

### Uninstall

```python
registry.uninstall_skill("url_summarizer")
```

Removes the skill from the registry. The skill files remain on disk for audit purposes.

---

## Built-in Skills

Lancelot ships with these SYSTEM skills:

| Skill | Permissions | Description |
|-------|------------|-------------|
| `command_runner` | `shell_exec`, `read_input`, `write_output` | Execute shell commands in sandbox |
| `repo_writer` | `file_write`, `file_read`, `read_input`, `write_output` | Git operations and file management |
| `network_client` | `network_fetch`, `network_post`, `read_input`, `write_output` | HTTP requests through governed pipeline |
| `service_runner` | `shell_exec`, `network_fetch`, `read_input`, `write_output` | Start and manage services |

These skills cannot be uninstalled but can be disabled via feature flags.

---

## Packaging for Distribution

To create a distributable package:

```python
from skills.governance import build_skill_package

build_skill_package(
    skill_name="url_summarizer",
    registry=registry,
    output_dir="./packages"
)
```

This creates a `.zip` archive containing the manifest, execute module, and all Python files.

---

## What NOT to Do

| Don't | Why |
|-------|-----|
| Underdeclare permissions | Your skill will be blocked at runtime when it tries ungoverned actions |
| Overdeclare permissions | Wastes trust opportunities and may delay approval |
| Make direct HTTP calls | Must go through the governed network pipeline |
| Store credentials in skill code | Use the Vault for all secrets |
| Bypass the sandbox | Sandbox enforcement is mandatory — attempting to escape generates security receipts |
| Skip the proposal pipeline | Direct installation without approval violates governance |

---

## Complete Example: Echo Skill

A minimal skill for testing:

**`skill.yaml`:**
```yaml
name: echo
version: "1.0.0"
description: "Returns the input text unchanged — useful for testing the skill pipeline"
inputs:
  - name: text
    type: string
    required: true
    description: "Text to echo back"
outputs:
  - name: echoed
    type: string
    description: "The echoed text"
risk: LOW
permissions:
  - read_input
  - write_output
scheduler_eligible: false
receipts:
  emit_on_success: true
  emit_on_failure: true
  include_inputs: true
  include_outputs: true
```

**`execute.py`:**
```python
def execute(inputs: dict) -> dict:
    return {"echoed": inputs["text"]}
```

**`test_echo.py`:**
```python
def test_echo():
    result = execute({"text": "hello"})
    assert result["echoed"] == "hello"
```

This skill requests only `read_input` and `write_output` — no elevated permissions, no approval friction, instant installation.
