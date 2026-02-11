"""
Skill Manifest Schema — declares capabilities, credentials, and domains.

Every skill must declare what it needs before installation.
The manifest is validated at install time (Stage 1 of the security pipeline).
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, field_validator, model_validator


# ── Capability / Credential Declarations ─────────────────────────

class SkillCapabilityDeclaration(BaseModel):
    """A capability the skill requires or optionally uses."""
    capability: str
    description: str
    required: bool = True


class SkillCredentialDeclaration(BaseModel):
    """A credential the skill needs from the Vault."""
    vault_key: str
    type: str
    purpose: str


# ── Known capability prefixes ────────────────────────────────────

_KNOWN_CAPABILITY_PREFIXES = (
    "connector.", "fs.", "shell.", "network.", "tool.",
    "credential.", "memory.", "schedule.",
)


# ── Skill Manifest ───────────────────────────────────────────────

class SkillManifest(BaseModel):
    """Full manifest for a skill, validated at install time."""
    id: str
    name: str
    version: str
    author: str
    source: str  # "first-party", "community", "user"
    description: str = ""
    capabilities_required: List[SkillCapabilityDeclaration]
    capabilities_optional: List[SkillCapabilityDeclaration] = []
    credentials: List[SkillCredentialDeclaration] = []
    target_domains: List[str] = []
    data_reads: List[str] = []
    data_writes: List[str] = []
    does_not_access: List[str] = []

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Skill id must not be empty")
        return v

    @field_validator("source")
    @classmethod
    def source_valid(cls, v: str) -> str:
        allowed = ("first-party", "community", "user")
        if v not in allowed:
            raise ValueError(f"source must be one of {allowed}, got '{v}'")
        return v

    @field_validator("capabilities_required")
    @classmethod
    def at_least_one_capability(cls, v: List[SkillCapabilityDeclaration]) -> List[SkillCapabilityDeclaration]:
        if not v:
            raise ValueError("At least one required capability must be declared")
        return v

    @field_validator("target_domains")
    @classmethod
    def no_wildcard_domains(cls, v: List[str]) -> List[str]:
        for domain in v:
            if "*" in domain:
                raise ValueError(f"Wildcard domains not allowed: '{domain}'")
        return v

    @model_validator(mode="after")
    def credentials_have_matching_domains(self) -> "SkillManifest":
        """Credentials declared but no target_domains → error."""
        if self.credentials and not self.target_domains:
            raise ValueError(
                "Credentials declared but no target_domains — "
                "if the skill needs auth, it must declare target domains"
            )
        return self

    @model_validator(mode="after")
    def does_not_access_required_for_community(self) -> "SkillManifest":
        """Community/user source must declare does_not_access."""
        if self.source in ("community", "user") and not self.does_not_access:
            raise ValueError(
                f"Skills with source='{self.source}' must declare does_not_access"
            )
        return self

    def all_capabilities(self) -> List[str]:
        """Return all capability strings (required + optional)."""
        return (
            [c.capability for c in self.capabilities_required]
            + [c.capability for c in self.capabilities_optional]
        )

    def all_vault_keys(self) -> List[str]:
        """Return all credential vault keys."""
        return [c.vault_key for c in self.credentials]

    def audit(self) -> List[dict]:
        """Audit manifest for issues. Returns list of findings."""
        findings = []

        # Error: unrecognized capability prefix
        for cap in self.all_capabilities():
            if not any(cap.startswith(p) for p in _KNOWN_CAPABILITY_PREFIXES):
                findings.append({
                    "level": "error",
                    "message": f"Unrecognized capability prefix: '{cap}'",
                })

        # Warning: write capabilities but no does_not_access
        write_caps = [c for c in self.all_capabilities() if ".write" in c or ".delete" in c]
        if write_caps and not self.does_not_access:
            findings.append({
                "level": "warning",
                "message": "Write/delete capabilities declared but no does_not_access list",
            })

        # Warning: >5 target domains
        if len(self.target_domains) > 5:
            findings.append({
                "level": "warning",
                "message": f"Overly broad: {len(self.target_domains)} target domains declared",
            })

        # Info: optional capabilities
        if self.capabilities_optional:
            findings.append({
                "level": "info",
                "message": f"{len(self.capabilities_optional)} optional capabilities declared",
            })

        return findings


# ── Validator Helper ─────────────────────────────────────────────

def validate_manifest(manifest_dict: dict) -> SkillManifest:
    """Parse and validate a manifest dict. Raises ValidationError on failure."""
    return SkillManifest.model_validate(manifest_dict)
