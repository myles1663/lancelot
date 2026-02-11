"""
APLAnalyzer — periodic analysis trigger.

Called after each manual decision. If enough new decisions since last
analysis, runs the full detection pipeline and generates proposals.
"""

from __future__ import annotations

import logging
from typing import List

from src.core.governance.approval_learning.config import APLConfig
from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import AutomationRule
from src.core.governance.approval_learning.pattern_detector import PatternDetector
from src.core.governance.approval_learning.rule_engine import RuleEngine

logger = logging.getLogger(__name__)


class APLAnalyzer:
    """Triggers pattern analysis and rule proposal generation."""

    def __init__(
        self,
        config: APLConfig,
        decision_log: DecisionLog,
        pattern_detector: PatternDetector,
        rule_engine: RuleEngine,
    ):
        self._config = config
        self._decision_log = decision_log
        self._detector = pattern_detector
        self._engine = rule_engine

    def maybe_analyze(self) -> List[AutomationRule]:
        """Run analysis if enough new decisions. Returns new proposals."""
        if not self._detector.should_analyze(self._decision_log):
            return []

        logger.info("APL: Running pattern analysis...")

        # Get decision window
        decisions = self._decision_log.get_window(
            self._config.detection.analysis_window_days
        )
        if not decisions:
            return []

        # Detect patterns
        patterns = self._detector.detect_all(decisions)

        # Generate proposals
        proposals = self._detector.generate_proposals(patterns, self._config)

        # Filter out declined patterns
        proposals = [
            p
            for p in proposals
            if not self._engine.is_pattern_declined(p.pattern_id)
        ]

        # Add proposals to rule engine
        added: List[AutomationRule] = []
        for proposal in proposals:
            try:
                self._engine.add_proposal(proposal)
                added.append(proposal)
                logger.info("APL: New proposal — %s", proposal.name)
            except ValueError as e:
                logger.debug("APL: Skipping proposal: %s", e)

        # Mark analysis complete
        self._decision_log.mark_analysis_complete()

        return added
