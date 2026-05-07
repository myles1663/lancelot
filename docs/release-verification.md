# Release Verification

This document describes the public checks a reviewer or release operator should run before treating a Lancelot source tree as a clean public release candidate.

## Source Tree Hygiene

Run:

```bash
python scripts/verify-public-release.py --public-artifact --skip-pytest --skip-uab --skip-docker
```

This verifies that the public tree does not track non-release artifacts and that source files pass the release hygiene checks encoded in `scripts/verify-public-release.py`.

## Dependency Lockfiles

The release tree includes lockfiles for the main dependency surfaces:

| Surface | Lockfile |
| --- | --- |
| Python runtime | `uv.lock` |
| War Room frontend | `src/warroom/package-lock.json` |
| UAB package | `packages/uab/package-lock.json` |

Use the lockfiles for reproducible local verification:

```bash
uv sync --frozen
npm --prefix src/warroom ci
npm --prefix packages/uab ci
```

The legacy `requirements.txt` file is kept as a compatibility fallback. The canonical Python dependency source is `pyproject.toml` plus `uv.lock`.

## Proof Tests

Run the focused proof suites first:

```bash
python -m pytest -q \
  tests/test_receipts.py \
  tests/hive/test_runtime.py \
  tests/test_kill_switch_contract.py \
  tests/test_gateway_health.py \
  tests/test_token_url_hardening.py::test_live_websocket_rejects_query_param_tokens
```

For the full Python release-readiness suite with coverage:

```bash
python -m pytest -q --cov=src --cov-report=term-missing --cov-report=json:coverage-full.json
```

The latest release-readiness pass recorded:

```text
7,216 passed, 24 skipped, 31 deselected
90.5085% Python line coverage
```

Then verify the UI and UAB surfaces:

```bash
npm --prefix src/warroom ci
npm --prefix src/warroom run type-check
npm --prefix src/warroom run build

npm --prefix packages/uab ci
npm --prefix packages/uab test
```

Finally validate Docker Compose:

```bash
if [ ! -f .env ]; then cp .env.example .env && cleanup_env=1; fi
docker compose config --quiet
if [ "${cleanup_env:-0}" = "1" ]; then rm .env; fi
```

## Docker Image Defaults

`docker-compose.yml` defaults to prebuilt images for convenience:

- `LANCELOT_CORE_IMAGE`
- `LOCAL_LLM_IMAGE`
- `BONSAI_LLM_IMAGE`

For casual evaluation, the documented defaults keep installation short. For controlled releases or production-style deployments, pin these environment variables to a specific tag or digest before rollout.

The Dockerfile pins several build-time tools directly, including the `uv` version and Codex CLI package version. If you build images locally, prefer a clean checkout and capture the resulting image digest in your release notes.

## Fresh Install And Prebuilt Image Smoke

A source tree can pass verification while the public install path is still broken. For a release tag, verify the published images from a fresh clone:

```bash
git clone https://github.com/myles1663/lancelot.git lancelot-smoke
cd lancelot-smoke
git checkout <release-tag>
docker compose pull lancelot-core local-llm
docker compose up -d --no-build
curl http://localhost:8000/health/live
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d '{"text":"hello"}'
```

Record the image tag or digest used for the smoke. If the release workflow did not publish the expected GHCR images, the release is not install-ready through the default `npx create-lancelot` / `docker compose pull` path.

## Release Candidate Checklist

- [ ] Public release verification passes.
- [ ] Focused Python proof tests pass.
- [ ] Full Python coverage run is reviewed for the release candidate.
- [ ] War Room typecheck and production build pass.
- [ ] UAB build and tests pass.
- [ ] Docker Compose config resolves.
- [ ] Fresh-clone installer tests pass.
- [ ] Release workflow publishes the expected prebuilt Docker images.
- [ ] Fresh-clone prebuilt-image smoke passes with `docker compose up -d --no-build`.
- [ ] Release notes list mature paths and known limitations.
- [ ] Docker image tags or digests are recorded for the release.
- [ ] The release is tagged from the public repository, not from the source working tree.
