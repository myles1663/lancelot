"""Startup configuration validation for operator-visible readiness.

The validator is intentionally deterministic: it only inspects configuration
that should be known at process start and classifies findings into operator
categories instead of relying on scattered boot warnings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlparse


PROVIDER_KEY_VARS: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-codex": "",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}

_LOCAL_MODEL_URL_VARS = (
    "LOCAL_LLM_URL",
    "LOCAL_LLM_BONSAI_1_7B_URL",
    "LOCAL_LLM_BONSAI_8B_URL",
    "LOCAL_LLM_SCRUB_REGION_FINDER_URL",
    "LOCAL_LLM_SCRUB_SEGMENT_VERIFIER_URL",
    "LOCAL_LLM_UTILITY_URL",
)
_LOCAL_MODEL_ALLOWED_HOSTS = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "host.docker.internal",
    "local-llm",
})
_HOST_BRIDGE_ALLOWED_HOSTS = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "host.docker.internal",
})


@dataclass(frozen=True)
class StartupValidationReport:
    """Structured startup configuration report exposed to logs and health."""

    provider: str
    auth_provider: str
    dev_mode: bool
    required_missing: list[str] = field(default_factory=list)
    optional_missing: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """True only when startup config has no blocked or degraded findings."""
        return not self.blocked and not self.degraded

    @property
    def can_start(self) -> bool:
        """True when the process can start without a fail-closed finding."""
        return not self.blocked

    def health_degraded_reasons(self) -> list[str]:
        """Reasons that should make readiness visibly degraded."""
        return [*self.blocked, *self.degraded]

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "can_start": self.can_start,
            "provider": self.provider,
            "auth_provider": self.auth_provider,
            "dev_mode": self.dev_mode,
            "required_missing": list(self.required_missing),
            "optional_missing": list(self.optional_missing),
            "degraded": list(self.degraded),
            "blocked": list(self.blocked),
            "warnings": list(self.warnings),
        }


_CURRENT_REPORT: StartupValidationReport | None = None


def _env_get(env: Mapping[str, str], key: str, default: str = "") -> str:
    value = env.get(key, default)
    return str(value).strip() if value is not None else ""


def _env_true(env: Mapping[str, str], key: str) -> bool:
    return _env_get(env, key).lower() in {"1", "true", "yes", "on"}


def _normalize_hostname(hostname: str) -> str:
    return (hostname or "").strip().lower().rstrip(".")


def _is_single_label_hostname(hostname: str) -> bool:
    if not hostname or "." in hostname:
        return False
    return not any(ch in hostname for ch in ":[]")


def _validate_local_url(
    env_var: str,
    url: str,
    *,
    allowed_hosts: frozenset[str],
    allow_single_label: bool = False,
) -> str | None:
    parsed = urlparse(url)
    hostname = _normalize_hostname(parsed.hostname or "")
    if parsed.scheme not in {"http", "https"} or not hostname:
        return f"{env_var} must be an http(s) URL inside the local control plane; got {url!r}"
    if hostname in allowed_hosts:
        return None
    if allow_single_label and _is_single_label_hostname(hostname):
        return None
    return (
        f"{env_var} points outside the local control-plane boundary: {url}. "
        "Use loopback, host.docker.internal, or an approved local service hostname."
    )


def validate_startup_environment(
    env: Mapping[str, str] | None = None,
    *,
    api_token: str | None = None,
    vault_configured: bool | None = None,
) -> StartupValidationReport:
    """Validate startup configuration and classify findings for operators."""
    if env is None:
        env = os.environ
    provider = (_env_get(env, "LANCELOT_PROVIDER", "gemini") or "gemini").lower()
    auth_provider = (_env_get(env, "LANCELOT_AUTH_PROVIDER", "local") or "local").lower()
    dev_mode = _env_true(env, "LANCELOT_DEV_MODE")

    required_missing: list[str] = []
    optional_missing: list[str] = []
    degraded: list[str] = []
    blocked: list[str] = []
    warnings: list[str] = []

    token = (api_token or _env_get(env, "LANCELOT_API_TOKEN")).strip()
    if not token:
        if dev_mode:
            warnings.append("LANCELOT_DEV_MODE=true allows API requests without LANCELOT_API_TOKEN.")
        else:
            required_missing.append("LANCELOT_API_TOKEN")
            blocked.append(
                "LANCELOT_API_TOKEN is missing and LANCELOT_DEV_MODE is not true; "
                "programmatic API requests will fail closed."
            )

    vault_key = _env_get(env, "LANCELOT_VAULT_KEY")
    has_vault_key = bool(vault_key) if vault_configured is None else bool(vault_configured)
    if not has_vault_key:
        if _env_true(env, "LANCELOT_ALLOW_EPHEMERAL_VAULT"):
            warnings.append("LANCELOT_ALLOW_EPHEMERAL_VAULT=true uses development-only vault key material.")
        else:
            required_missing.append("LANCELOT_VAULT_KEY")
            blocked.append(
                "LANCELOT_VAULT_KEY is missing; credential vault persistence is not production-safe."
            )

    if provider not in PROVIDER_KEY_VARS:
        blocked.append(
            f"LANCELOT_PROVIDER={provider!r} is unsupported. "
            f"Supported providers: {', '.join(sorted(PROVIDER_KEY_VARS))}."
        )
    else:
        key_var = PROVIDER_KEY_VARS[provider]
        if key_var and not _env_get(env, key_var):
            if (
                provider == "gemini"
                and _env_get(env, "LANCELOT_AUTH_MODE").upper() == "OAUTH"
                and _env_get(env, "GOOGLE_APPLICATION_CREDENTIALS")
            ):
                pass
            else:
                optional_missing.append(key_var)
                degraded.append(
                    f"{key_var} is missing for LANCELOT_PROVIDER={provider}; "
                    "frontier model routing will be degraded until credentials are configured."
                )
        elif provider == "openai-codex":
            warnings.append(
                "openai-codex uses mounted Codex OAuth credentials or the provider vault; "
                "no platform API key is expected at startup."
            )

    if auth_provider == "local":
        if not _env_get(env, "WARROOM_USERNAME"):
            optional_missing.append("WARROOM_USERNAME")
        if not _env_get(env, "WARROOM_PASSWORD"):
            optional_missing.append("WARROOM_PASSWORD")
        if "WARROOM_USERNAME" in optional_missing or "WARROOM_PASSWORD" in optional_missing:
            warnings.append(
                "Local War Room credentials are incomplete; onboarding or the installer must finish local auth setup."
            )
    elif auth_provider == "oidc":
        missing_oidc = [
            key for key in ("OIDC_ISSUER_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET")
            if not _env_get(env, key)
        ]
        optional_missing.extend(missing_oidc)
        if missing_oidc:
            degraded.append(
                "LANCELOT_AUTH_PROVIDER=oidc but OIDC configuration is incomplete: "
                + ", ".join(missing_oidc)
            )
    else:
        blocked.append(
            f"LANCELOT_AUTH_PROVIDER={auth_provider!r} is unsupported. Use 'local' or 'oidc'."
        )

    local_url_defaults = {"LOCAL_LLM_URL": "http://local-llm:8080"}
    for env_var in _LOCAL_MODEL_URL_VARS:
        value = _env_get(env, env_var, local_url_defaults.get(env_var, ""))
        if not value:
            continue
        issue = _validate_local_url(
            env_var,
            value,
            allowed_hosts=_LOCAL_MODEL_ALLOWED_HOSTS,
            allow_single_label=True,
        )
        if issue:
            blocked.append(issue)

    for env_var, default in (
        ("HOST_AGENT_URL", "http://host.docker.internal:9111"),
        ("UAB_DAEMON_URL", "http://host.docker.internal:7900"),
    ):
        value = _env_get(env, env_var, default)
        issue = _validate_local_url(
            env_var,
            value,
            allowed_hosts=_HOST_BRIDGE_ALLOWED_HOSTS,
        )
        if issue:
            blocked.append(issue)

    return StartupValidationReport(
        provider=provider,
        auth_provider=auth_provider,
        dev_mode=dev_mode,
        required_missing=sorted(set(required_missing)),
        optional_missing=sorted(set(optional_missing)),
        degraded=degraded,
        blocked=blocked,
        warnings=warnings,
    )


def set_startup_validation_report(report: StartupValidationReport) -> None:
    global _CURRENT_REPORT
    _CURRENT_REPORT = report


def get_startup_validation_report() -> StartupValidationReport | None:
    return _CURRENT_REPORT


def startup_validation_ready() -> bool:
    report = get_startup_validation_report()
    return bool(report and report.ready)


def startup_validation_health_details() -> dict:
    report = get_startup_validation_report()
    if report is None:
        return {
            "startup_validation_ready": False,
            "startup_validation": {
                "ready": False,
                "blocked": ["Startup validation report unavailable"],
                "degraded": [],
                "warnings": [],
            },
            "startup_validation_degraded_reasons": ["Startup validation report unavailable"],
        }
    return {
        "startup_validation_ready": report.ready,
        "startup_validation": report.to_dict(),
        "startup_validation_degraded_reasons": report.health_degraded_reasons(),
    }
