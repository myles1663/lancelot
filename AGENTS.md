# AGENTS.md - Lancelot Repo Instructions for Codex

This repository follows a private-dev/public-release two-repo workflow.

## Repo model
- Private dev repo: `origin` -> `https://github.com/myles1663/lancelot-dev.git`
- Public release repo: `public` -> `https://github.com/myles1663/lancelot.git`
- Do normal development work only in the private dev repo.
- Never push to `public` unless Myles explicitly says the project is ready to release.

## Day-to-day Git rules
- Commit freely and often to `origin`.
- Push after each meaningful working state.
- Stage specific files only. Do not use `git add -A` or `git add .`.
- Never commit `.env`, credentials, secrets, or unnecessary large generated artifacts.
- Use specific commit messages that say what changed and why.
- Reference module paths, classes, feature flags, or endpoints when helpful.

Standard workflow:
```bash
git add <specific files>
git commit -m "descriptive message"
git push origin master
```

## Stable-state tagging
- Tag stable checkpoints in the private repo using `dev-v{version}.{increment}`.
- Tag after confirmed working states, before risky changes, and after successful rebuild plus smoke test.
- Never delete tags.
- Force-push `origin/master` only during an explicitly approved rollback.

## Documentation policy
- Documentation is part of implementation, not follow-up work.
- Always update `CHANGELOG.md` under `[Unreleased]`.
- Audit and update all relevant docs whenever architecture, behavior, feature flags, receipts, UI flows, connectors, Hive, UAB, observability, time-travel, A2A, incident response, or release-facing behavior changes.
- If a feature warrants a doc that does not yet exist, create it.

## Release policy
- Only execute a public release when Myles explicitly says "ready to release" or "merge to public".
- Public releases are squash-merged from private work.
- The public release commit message is always provided by Myles.
- Never push individual dev commits to `public`.
- Never force-push `public/main` without explicit instruction.

Release flow:
```bash
git checkout -b release-prep
git merge --squash master
git commit -m "[RELEASE MESSAGE PROVIDED BY MYLES]"
git push public release-prep:main
git checkout master
git branch -d release-prep
```

## Source of truth
- The fuller SOP is maintained in `C:\Users\SSAdministrator\Desktop\Lancelot_Git_SOP.md`.
- `CLAUDE.md` should stay aligned with this policy.
