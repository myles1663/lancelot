# Enterprise Development and Publishing Flow

Use this reference for branch, PR, merge, release, publishing, hotfix, public sync, or repo-governance work in Lancelot.

## 1. Enterprise Standard

The repository flow must preserve four properties:

- `main` is always releasable.
- Every material change is reviewed, tested, traceable, and reversible.
- Release artifacts can be tied back to reviewed commits, CI results, tags, image digests, and verification evidence.
- Public publishing excludes internal-only artifacts, private campaign language, secrets, local logs, and unverified claims.

Direct-to-main work, unreviewed releases, undocumented runtime changes, and source-only release validation are not acceptable for production enterprise software.

## 2. Branch Model

Use trunk-based development with short-lived branches:

- `main`: protected, stable, releasable.
- `feat/<slug>`: new capability.
- `fix/<slug>`: defect correction.
- `chore/<slug>`: maintenance.
- `docs/<slug>`: documentation-only work.
- `spec/<slug>`: spec or blueprint work.
- `hotfix/<slug>`: urgent repair from `main`.

Rules:

- One branch equals one logical change or upgrade slice.
- Use neutral, implementation-focused branch names.
- Avoid names tied to demos, investors, campaigns, customers, or launch events.
- If scope grows, stop and create a separate spec, branch, and PR.
- Never rebase or force-push `main`.

## 3. Spec and Design Gate

For new subsystems, background processes, persistent state, runtime dependencies, governance behavior, model routing, connectors, War Room control surfaces, publishing behavior, or production claims, create or update the appropriate design material before implementation.

The design note, spec, or blueprint should answer:

- What problem does this solve?
- Which subsystem owns it?
- What data does it read and write?
- What receipts does it emit?
- What governance gates apply?
- What feature flag or kill switch controls it?
- What is the rollback path?
- What is out of scope?
- What verification proves it works?

Do not edit an active in-progress spec to absorb new ideas. Create a new upgrade slice.

## 4. Development Flow

1. Start from current `main` or the approved development source branch.
2. Create a short-lived scoped branch.
3. Sync early: pull/rebase from `main` before opening a PR and before major pushes.
4. Implement the smallest coherent change.
5. Add focused tests for the changed contract and negative paths.
6. Update docs, config examples, runbooks, changelog, and release notes as appropriate.
7. Run local verification before PR.
8. Push the branch and open a PR.

Preferred local sync:

```powershell
git fetch origin
git rebase origin/main
```

If the repo policy or branch history favors merge commits inside feature branches, merge `origin/main` into the branch instead. Do not rewrite shared branch history after review starts unless the team expects it.

## 5. PR Requirements

Every PR must include:

- Summary of the change.
- Affected subsystems.
- Governance impact.
- Receipt impact.
- Feature flag or kill-switch behavior.
- Test evidence.
- Rollout and rollback notes.
- Screenshots or browser verification for War Room UI changes.
- Public artifact assessment for release-facing or public-repo changes.
- Patent-sensitive architecture note when touching Soul, risk tiers, trust graduation, connector proxy, kill-switch dependency resolution, or similarly protected architecture.

Required checklist for substantial runtime changes:

- Spec or design note created.
- Blueprint or implementation plan created when needed.
- Feature gated where runtime behavior changes.
- Contracts/interfaces defined.
- Unit tests added.
- Integration tests added or intentionally deferred with reason.
- Receipts emitted for state changes, failures, skips, and governed actions.
- War Room/API status visibility added when operator expectations change.
- Runbook updated for operational behavior.
- `CHANGELOG.md` updated for user-visible behavior.

## 6. CI and Review Gate

Before merge:

- Required CI jobs must pass.
- Reviewer concerns must be resolved.
- Security, governance, auth, persistence, and release risks must be addressed or explicitly deferred.
- The branch must be current enough with `main` that CI reflects the intended merge result.
- Generated artifacts must be intentional and minimal.
- Secrets, `.env`, local logs, coverage dumps, node modules, caches, and private/internal-only artifacts must not be included.

For public release PRs, Python line coverage must be at least 90.00% before merge. Treat coverage below 90.00% as a release blocker unless the owner explicitly approves a documented exception in the PR with scope, reason, risk, and follow-up.

Recommended protected branch settings for `main`:

- Require PR before merge.
- Require approval.
- Require status checks.
- Require branches to be up to date before merge, when practical.
- Disallow direct pushes.
- Disallow force pushes.
- Restrict deletion.
- Require CODEOWNERS or domain-owner review for sensitive areas.

## 7. Merge Policy

Preferred merge path:

1. Confirm PR approval and passing CI.
2. Confirm the final PR description has accurate test and rollout evidence.
3. Squash merge into `main`.
4. Use a clean, release-facing commit message.
5. Delete the feature branch after merge.
6. Pull updated `main` locally.

Use rebase merge only for already-clean histories where each commit is independently meaningful. Avoid merge commits unless needed for a deliberate integration branch.

Squash commit messages should be concise and neutral:

```text
feat: add governed scheduler receipt visibility
fix: fail closed on public host-agent URLs
docs: add production release verification gate
chore: tighten public artifact verification
```

## 8. Hotfix Flow

For broken `main` or urgent production repair:

1. Branch from `main` using `hotfix/<slug>` or `fix/<slug>`.
2. Make the minimal correction only.
3. Add a regression test or focused proof check.
4. Open a fast PR with impact, rollback, and verification.
5. Squash merge after CI/review.
6. Tag or patch-release only after the release verification gate passes.

Do not bundle opportunistic cleanup with hotfixes.

## 9. Public Release and Publishing Flow

Use this when publishing from a private/source repository to a public release repository or public artifact channel.

1. Complete development on a short-lived branch in the source repository.
2. Open a PR into source `main`.
3. Require review, CI, governance review, and release-readiness evidence.
4. Squash merge into source `main`.
5. Create a short-lived public sync branch in the public release repository.
6. Apply only approved public-safe changes from the source merge.
7. Run the public artifact guard.
8. Run focused proof tests and release verification from the public release repository.
9. Open a PR into public `main`.
10. Require review and CI.
11. Confirm full Python line coverage is at least 90.00%.
12. Squash merge with a clean release-facing commit message.
13. Tag the release from the public repository after verification passes.
14. Wait for release workflows to publish images/packages.
15. Verify exact image tags or digests from a fresh clone.
16. Record verification evidence in the PR closeout or release notes.

Use neutral public sync branch names:

- `public-release-readiness`
- `runtime-readiness`
- `release-verification`
- `docs-release-readiness`

Avoid campaign, audience, customer, investor, or marketing-event names.

## 10. Public Artifact Guard

Before opening a public release PR, run the public artifact guard:

```powershell
python scripts/verify-public-release.py --public-artifact --skip-pytest --skip-uab --skip-docker
```

This should reject internal-only artifacts such as `docs/internal/`, private process notes, local logs, secrets, and other files that must not ship publicly.

## 11. Release Verification Gate

Before tagging:

```powershell
python scripts/verify-public-release.py --public-artifact --skip-pytest --skip-uab --skip-docker
python -m pytest -q tests/test_receipts.py tests/hive/test_runtime.py tests/test_kill_switch_contract.py tests/test_gateway_health.py
npm --prefix installer ci
npm --prefix installer test
npm --prefix src/warroom ci
npm --prefix src/warroom run type-check
npm --prefix src/warroom run build
npm --prefix packages/uab ci
npm --prefix packages/uab test
docker compose config --quiet
```

For high-risk releases, also run the full Python gate:

```powershell
python -m pytest -q --cov=src --cov-report=term-missing --cov-report=json:coverage-full.json
```

For every public release PR, run the full Python coverage gate and confirm the total line coverage is at least 90.00% before merge:

```powershell
python -m pytest -q --cov=src --cov-report=term-missing --cov-report=json:coverage-full.json
```

If the result is below 90.00%, either add tests until coverage recovers or document an owner-approved exception in the PR. Do not silently merge below the threshold.

If Docker is unavailable on the release operator machine, use GitHub Actions or another clean Docker host. Do not substitute source-only tests for install or image verification.

## 12. Tagging and Image Verification

Tags must be created from the verified public release commit, not from a dirty source working tree.

Before tagging:

- `VERSION` matches the intended `v*` tag.
- `CHANGELOG.md` and `RELEASE_NOTES.md` match the release.
- CI is green on the public PR.
- Public artifact guard passes.
- Python line coverage is at least 90.00% or an owner-approved exception is documented.
- Release verification evidence is recorded.

After tagging:

1. Confirm the release workflow completed successfully.
2. Confirm GHCR or the configured registry has expected image tags.
3. Pull exact release images from a fresh clone.
4. Start the stack using prebuilt images, not a local build.
5. Verify health and a real user smoke path.
6. Record image tags or digests.

Fresh-clone prebuilt image smoke:

```powershell
git clone https://github.com/myles1663/lancelot.git lancelot-smoke
Set-Location lancelot-smoke
git checkout <release-tag>
docker compose pull lancelot-core local-llm
docker compose up -d --no-build
curl.exe http://localhost:8000/health/live
curl.exe -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"text\":\"hello\"}"
```

## 13. Release Closeout Evidence

A release or publishing closeout should include:

- Source PR link and squash commit.
- Public PR link and squash commit, if public sync applies.
- Release tag.
- CI workflow links or summarized job results.
- Public artifact guard result.
- Python proof/full test result, including total line coverage percentage.
- War Room build result.
- UAB test result.
- Docker Compose config result.
- Fresh-clone install or prebuilt-image smoke result.
- Image tags or digests.
- Known limitations and rollback path.

## 14. Stop Conditions

Stop and correct the process if:

- Work is headed directly to `main`.
- CI is bypassed.
- Review is bypassed.
- A public PR is about to merge below 90.00% Python line coverage without an owner-approved documented exception.
- A public release includes internal-only artifacts.
- A release tag is created before verification.
- Published images are not verified.
- A feature changes governance, memory, tool execution, auth, connector behavior, or production claims without tests and rollback notes.
- The PR hides known failures or uses vague test evidence.

Enterprise quality requires boring, repeatable proof. If proof is missing, the release is not ready.
