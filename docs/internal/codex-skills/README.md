# Internal Codex Skills

This directory stores private Codex skills for Lancelot maintainers and hired developers.

Do not copy this directory to the public release repository. The public artifact guard treats `docs/internal/` as private-only material and should block a public release tree if this path is present.

## Install

From a local private checkout, copy a skill folder into your Codex skills directory:

```powershell
Copy-Item -Recurse -Force .\docs\internal\codex-skills\lancelot-repo-sop $env:USERPROFILE\.codex\skills\
```

Restart Codex after copying the skill so it can be discovered.
