"""
Skill Security Pipeline — 6-stage orchestrator.

Stages:
1. Manifest validation
2. Static analysis
3. Sandbox testing
4. Owner review (external — returns results for approval)
5. Capability enforcement (registration after approval)
6. Trust initialization (if trust ledger available)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from src.skills.security.manifest import SkillManifest, validate_manifest
from src.skills.security.static_analyzer import StaticAnalyzer, StaticAnalysisResult
from src.skills.security.sandbox_tester import SandboxTester, SandboxTestResult
from src.skills.security.capability_enforcer import CapabilityEnforcer

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result from running the skill security pipeline."""
    skill_id: str
    passed: bool
    stage_results: Dict[str, Any] = field(default_factory=dict)
    failed_at_stage: str = ""
    approved_capabilities: List[str] = field(default_factory=list)
    manifest: Optional[SkillManifest] = None


class SkillSecurityPipeline:
    """Orchestrates all 6 stages of skill security evaluation."""

    def __init__(
        self,
        static_analyzer: StaticAnalyzer,
        sandbox_tester: SandboxTester,
        capability_enforcer: CapabilityEnforcer,
        trust_ledger: Any = None,
    ) -> None:
        self._static_analyzer = static_analyzer
        self._sandbox_tester = sandbox_tester
        self._capability_enforcer = capability_enforcer
        self._trust_ledger = trust_ledger

    def evaluate(
        self, skill_path: Path, manifest_dict: dict
    ) -> PipelineResult:
        """Run stages 1-3 and return results for owner review.

        Does NOT install the skill — call approve_and_install() after review.
        """
        skill_path = Path(skill_path)
        stage_results: Dict[str, Any] = {}

        # Stage 1: Manifest validation
        try:
            manifest = validate_manifest(manifest_dict)
            stage_results["manifest"] = {"passed": True, "audit": manifest.audit()}
        except (ValidationError, ValueError) as e:
            return PipelineResult(
                skill_id=manifest_dict.get("id", "unknown"),
                passed=False,
                stage_results={"manifest": {"passed": False, "error": str(e)}},
                failed_at_stage="manifest",
            )

        skill_id = manifest.id

        # Stage 2: Static analysis
        static_result = self._static_analyzer.analyze(skill_path, skill_id)
        stage_results["static_analysis"] = {
            "passed": static_result.passed,
            "critical_count": static_result.critical_count,
            "warning_count": static_result.warning_count,
            "total_files": static_result.total_files_scanned,
        }
        if not static_result.passed:
            return PipelineResult(
                skill_id=skill_id,
                passed=False,
                stage_results=stage_results,
                failed_at_stage="static_analysis",
                manifest=manifest,
            )

        # Stage 3: Sandbox testing
        sandbox_result = self._sandbox_tester.test_skill(skill_path, manifest)
        stage_results["sandbox_test"] = {
            "passed": sandbox_result.passed,
            "violations": sandbox_result.violations,
            "operations_tested": sandbox_result.operations_tested,
            "details": sandbox_result.details,
        }
        if not sandbox_result.passed:
            return PipelineResult(
                skill_id=skill_id,
                passed=False,
                stage_results=stage_results,
                failed_at_stage="sandbox_test",
                manifest=manifest,
            )

        # Stage 4: Return for owner review
        stage_results["owner_review"] = {"status": "pending"}

        return PipelineResult(
            skill_id=skill_id,
            passed=True,
            stage_results=stage_results,
            manifest=manifest,
        )

    def approve_and_install(
        self,
        pipeline_result: PipelineResult,
        approved_capabilities: List[str],
    ) -> bool:
        """Stage 5-6: Register approved capabilities and initialize trust.

        Called after owner reviews and approves the pipeline result.
        """
        if not pipeline_result.passed or pipeline_result.manifest is None:
            return False

        manifest = pipeline_result.manifest
        skill_id = manifest.id

        # Stage 5: Register in capability enforcer
        self._capability_enforcer.register_skill(skill_id, manifest)
        pipeline_result.approved_capabilities = approved_capabilities

        # Stage 6: Initialize trust records
        if self._trust_ledger is not None:
            try:
                from src.core.governance.models import RiskTier
                # Create mock operations for trust initialization
                for cap in approved_capabilities:
                    self._trust_ledger.get_or_create_record(
                        capability=f"skill.{skill_id}.{cap}",
                        scope="default",
                        default_tier=RiskTier.T2_CONTROLLED,
                    )
            except Exception as e:
                logger.warning("Failed to initialize trust for %s: %s", skill_id, e)

        logger.info("Skill %s installed with %d capabilities", skill_id, len(approved_capabilities))
        return True

    def uninstall(self, skill_id: str) -> None:
        """Remove a skill from the capability enforcer."""
        self._capability_enforcer.unregister_skill(skill_id)
        logger.info("Skill %s uninstalled", skill_id)
