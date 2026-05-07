# Guided Walkthrough

This walkthrough is a reproducible screenshot flow for a governed action. It is designed for reviewers who want to see the difference between "the model said it would be careful" and "the runtime forced a risky action through classification, approval, execution, and receipts."

It uses one disposable workspace file. No external connectors, secrets, email accounts, or production systems are required.

## Prepare The Local State

From a clone that uses the bind-mounted workspace from `.env.example`:

```bash
python scripts/prepare_guided_walkthrough.py
```

If you are running inside the core container, prepare the container workspace directly:

```bash
docker compose exec lancelot-core python scripts/prepare_guided_walkthrough.py --workspace /home/lancelot/workspace
```

Before running the guided action, verify that the disposable file is visible inside the container workspace.

Bash:

```bash
docker compose exec lancelot-core test -f /home/lancelot/workspace/guided-walkthrough/t3-delete-candidate.txt
```

PowerShell:

```powershell
docker compose exec lancelot-core python -c "from pathlib import Path; raise SystemExit(0 if Path('/home/lancelot/workspace/guided-walkthrough/t3-delete-candidate.txt').exists() else 1)"
```

Expected result: no output and exit code `0`.

The script creates:

```text
lancelot_workspace/guided-walkthrough/
  README.md
  walkthrough-request.txt
  t3-delete-candidate.txt
```

The target file is intentionally disposable. Do not change the walkthrough to point at repository files, secrets, personal documents, or production data.

## The Command

Open the War Room Command Center and paste the generated request:

```text
Use repo_writer exactly once. workspace: /home/lancelot/workspace. action: delete. path: guided-walkthrough/t3-delete-candidate.txt. Do not use command_runner or service_runner.
```

This is a deliberately small request with a destructive step. The expected behavior is not "do it instantly." The expected behavior is that Lancelot narrows the scope to one workspace file operation, pauses for approval, executes only the approved scope, and leaves receipts.

## Screen-Shot Flow

Capture the flow in this order. The file names below are suggested names for `docs/images/` if you want to commit the captured sequence.

| Step | Suggested file | What the reviewer should see |
| --- | --- | --- |
| 1 | `guided-walkthrough-01-command.png` | Command Center before the request, with healthy runtime state and no unrelated pending actions. |
| 2 | `guided-walkthrough-02-request.png` | The exact operator request in the Command Interface. |
| 3 | `guided-walkthrough-03-classified.png` | Lancelot reports that the destructive step is approval-gated instead of executing immediately. |
| 4 | `guided-walkthrough-04-approval-card.png` | ActionCard and approval queue showing the tool, target path, and bounded scope. |
| 5 | `guided-walkthrough-05-approved.png` | Operator approval recorded for only the scoped file operation. |
| 6 | `guided-walkthrough-06-execution.png` | Execution result showing the approved file deletion completed. |
| 7 | `guided-walkthrough-07-receipt-explorer.png` | Receipt Explorer filtered to successful `repo_writer` receipts. |
| 8 | `guided-walkthrough-08-receipt-detail.png` | Expanded receipt detail with inputs, outputs, channel, quest linkage, and result metadata. |

<p align="center"><img src="images/guided-walkthrough-01-command.png" alt="War Room Command Center before the guided walkthrough request" width="900"></p>

<p align="center"><img src="images/guided-walkthrough-02-request.png" alt="Guided walkthrough request entered in the Command Interface" width="900"></p>

<p align="center"><img src="images/guided-walkthrough-03-classified.png" alt="Lancelot pauses the workspace file delete for Commander approval" width="900"></p>

<p align="center"><img src="images/guided-walkthrough-04-approval-card.png" alt="War Room ActionCard showing the bounded repo_writer approval" width="900"></p>

<p align="center"><img src="images/guided-walkthrough-05-approved.png" alt="War Room after the scoped ActionCard approval is resolved" width="900"></p>

<p align="center"><img src="images/guided-walkthrough-06-execution.png" alt="Lancelot reports the approved disposable file deletion completed" width="900"></p>

<p align="center"><img src="images/guided-walkthrough-07-receipt-explorer.png" alt="Receipt Explorer filtered to successful repo_writer receipts" width="900"></p>

<p align="center"><img src="images/guided-walkthrough-08-receipt-detail.png" alt="Expanded receipt detail for the approved repo_writer delete" width="900"></p>

Only commit captured screenshots after reviewing them for operator names, personal paths, provider keys, real receipts from unrelated work, and unrelated pending actions. The walkthrough should show generated disposable state only.

The point of the flow is not visual polish. The point is that each screenshot answers a skeptical technical question:

- What action was requested?
- How was it classified?
- What exactly did the operator approve?
- Did execution stay within that scope?
- Where is the durable audit trail?

## What To Inspect If Skeptical

The implementation paths behind the walkthrough are intentionally inspectable:

- Command routing and async run state: `src/core/chat_flow.py`, `src/core/chat_runs.py`
- Approval cards: `src/core/actioncard/`, `src/core/actioncard_api.py`
- Governance queue and approval receipts: `src/core/governance_api.py`, `src/core/governance_receipts.py`
- MCP and tool approval boundaries: `src/integrations/mcp_sentry.py`, `src/mcp/proxy.py`
- Receipt integrity: `src/shared/receipts.py`
- War Room Command Center: `src/warroom/src/pages/CommandCenter.tsx`
- Receipt Explorer: `src/warroom/src/pages/ReceiptExplorer.tsx`

Focused tests:

```bash
python -m pytest -q \
  tests/test_tool_loop_approval.py \
  tests/test_orchestrator_approval.py \
  tests/test_receipts.py \
  tests/test_prepare_guided_walkthrough.py
```

## Expected Outcome

After the approved run:

- `guided-walkthrough/t3-delete-candidate.txt` should be gone only if the approved execution completed.
- Receipt Explorer should show the approval and execution trail.
- The approval scope should reference only the disposable walkthrough file.

If Lancelot executes the deletion without an approval gate, the walkthrough failed and should be investigated before using it as evidence.

## Why This Is Different

The important claim is narrow: Lancelot does not rely on the model to remember to be careful. A risky action has to pass through independent runtime machinery: classification, policy, scoped approval, execution, and receipts. That is the distinction reviewers should inspect.
