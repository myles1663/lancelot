"""
Capability Enforcer — runtime boundary enforcement for skills.

Every skill action is checked against its declared capabilities,
target domains, and credential vault keys before execution.
Undeclared actions are blocked with a PermissionError.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class EnforcementResult:
    """Result of a capability enforcement check."""
    allowed: bool
    skill_id: str
    action_capability: str
    reason: str = ""
    violation_type: str = ""  # "capability", "domain", "credential", "scope"


class CapabilityEnforcer:
    """Enforces declared capabilities at runtime."""

    def __init__(self) -> None:
        self._approved_capabilities: Dict[str, Set[str]] = {}
        self._approved_domains: Dict[str, Set[str]] = {}
        self._approved_vault_keys: Dict[str, Set[str]] = {}
        self._active_skill: Optional[str] = None
        self._violation_log: list = []

    def register_skill(self, skill_id: str, manifest) -> None:
        """Populate approved sets from a SkillManifest."""
        self._approved_capabilities[skill_id] = set(manifest.all_capabilities())
        self._approved_domains[skill_id] = set(
            getattr(manifest, "target_domains", [])
        )
        self._approved_vault_keys[skill_id] = set(manifest.all_vault_keys())
        logger.info(
            "Registered skill %s: %d capabilities, %d domains, %d vault keys",
            skill_id,
            len(self._approved_capabilities[skill_id]),
            len(self._approved_domains[skill_id]),
            len(self._approved_vault_keys[skill_id]),
        )

    def unregister_skill(self, skill_id: str) -> None:
        """Remove all approvals for a skill."""
        self._approved_capabilities.pop(skill_id, None)
        self._approved_domains.pop(skill_id, None)
        self._approved_vault_keys.pop(skill_id, None)
        logger.info("Unregistered skill %s", skill_id)

    def enforce(
        self,
        skill_id: str,
        capability: str,
        target_domain: str = "",
        vault_key: str = "",
    ) -> EnforcementResult:
        """Check if a skill action is allowed."""
        # Check capability
        approved_caps = self._approved_capabilities.get(skill_id, set())
        if not approved_caps:
            return EnforcementResult(
                allowed=False,
                skill_id=skill_id,
                action_capability=capability,
                reason=f"Skill '{skill_id}' is not registered",
                violation_type="capability",
            )

        if capability not in approved_caps:
            result = EnforcementResult(
                allowed=False,
                skill_id=skill_id,
                action_capability=capability,
                reason=f"Capability '{capability}' not declared by skill '{skill_id}'",
                violation_type="capability",
            )
            self._violation_log.append(result)
            return result

        # Check domain
        if target_domain:
            approved_domains = self._approved_domains.get(skill_id, set())
            if target_domain not in approved_domains:
                result = EnforcementResult(
                    allowed=False,
                    skill_id=skill_id,
                    action_capability=capability,
                    reason=f"Domain '{target_domain}' not declared by skill '{skill_id}'",
                    violation_type="domain",
                )
                self._violation_log.append(result)
                return result

        # Check vault key
        if vault_key:
            approved_keys = self._approved_vault_keys.get(skill_id, set())
            if vault_key not in approved_keys:
                result = EnforcementResult(
                    allowed=False,
                    skill_id=skill_id,
                    action_capability=capability,
                    reason=f"Vault key '{vault_key}' not declared by skill '{skill_id}'",
                    violation_type="credential",
                )
                self._violation_log.append(result)
                return result

        return EnforcementResult(
            allowed=True,
            skill_id=skill_id,
            action_capability=capability,
        )

    def list_approvals(self, skill_id: str) -> dict:
        """Return approved capabilities, domains, and vault keys for a skill."""
        return {
            "capabilities": sorted(self._approved_capabilities.get(skill_id, set())),
            "domains": sorted(self._approved_domains.get(skill_id, set())),
            "vault_keys": sorted(self._approved_vault_keys.get(skill_id, set())),
        }

    def create_enforcement_hook(self) -> callable:
        """Returns a function that checks actions before execution.

        Raises PermissionError for undeclared actions.
        """
        def hook(skill_id: str, capability: str,
                 target_domain: str = "", vault_key: str = "") -> EnforcementResult:
            result = self.enforce(skill_id, capability, target_domain, vault_key)
            if not result.allowed:
                raise PermissionError(
                    f"Skill {skill_id}: {result.reason}"
                )
            return result
        return hook

    def set_active_skill(self, skill_id: Optional[str]) -> None:
        """Set which skill is currently executing."""
        self._active_skill = skill_id

    def get_active_skill(self) -> Optional[str]:
        """Return the currently executing skill ID."""
        return self._active_skill
