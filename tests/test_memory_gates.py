"""
Tests for Memory vNext Write Gates.

These tests validate:
- Block allowlist enforcement
- Provenance validation
- Secret detection and scrubbing
- Quarantine-by-default logic
- Confidence threshold enforcement
"""

import os
import pytest

# Enable feature flag for testing
os.environ["FEATURE_MEMORY_VNEXT"] = "true"

from src.core.memory.schemas import (
    CoreBlockType,
    MemoryEdit,
    MemoryEditOp,
    MemoryStatus,
    Provenance,
    ProvenanceType,
)
from src.core.memory.gates import (
    WriteGateValidator,
    GateResult,
    QuarantineManager,
)
from src.core.memory.config import MemoryConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def validator():
    """Provide a default write gate validator."""
    return WriteGateValidator()


@pytest.fixture
def strict_validator():
    """Provide a strict validator with all gates enabled."""
    config = MemoryConfig(
        quarantine_by_default=True,
        require_provenance=True,
        min_confidence_core=0.8,
        min_confidence_archival=0.5,
    )
    return WriteGateValidator(config=config)


def create_edit(
    target: str,
    op: MemoryEditOp = MemoryEditOp.replace,
    after: str = "New content",
    confidence: float = 0.8,
    provenance: list[Provenance] | None = None,
) -> MemoryEdit:
    """Create a test memory edit."""
    return MemoryEdit(
        op=op,
        target=target,
        after=after,
        reason="Test edit",
        confidence=confidence,
        provenance=provenance or [],
    )


# ---------------------------------------------------------------------------
# Basic Validation Tests
# ---------------------------------------------------------------------------
class TestBasicValidation:
    """Tests for basic edit validation."""

    def test_validate_allowed_edit(self, validator):
        """Test validating an allowed edit."""
        edit = create_edit(
            target="core:mission",
            after="New mission content",
        )
        result = validator.validate_edit(edit, editor="agent")

        assert result.allowed is True

    def test_validate_item_edit(self, validator):
        """Test validating an item edit."""
        edit = create_edit(
            target="working:item123",
            after="Task content",
        )
        result = validator.validate_edit(edit, editor="agent")

        assert result.allowed is True

    def test_invalid_block_type(self, validator):
        """Test validation fails for invalid block type."""
        edit = create_edit(target="core:invalid_type")
        result = validator.validate_edit(edit, editor="agent")

        assert result.allowed is False
        assert "Invalid core block type" in result.reason


# ---------------------------------------------------------------------------
# Allowlist Tests
# ---------------------------------------------------------------------------
class TestAllowlist:
    """Tests for block allowlist enforcement."""

    def test_agent_can_edit_mission(self, validator):
        """Test agents can edit mission block."""
        edit = create_edit(target="core:mission")
        result = validator.validate_edit(edit, editor="agent")

        assert result.allowed is True

    def test_agent_cannot_edit_human(self, validator):
        """Test agents cannot edit human block without approval."""
        edit = create_edit(target="core:human")
        result = validator.validate_edit(edit, editor="agent")

        assert result.allowed is False
        assert "requires owner approval" in result.reason

    def test_agent_cannot_edit_persona(self, validator):
        """Test agents cannot edit persona block."""
        edit = create_edit(target="core:persona")
        result = validator.validate_edit(edit, editor="agent")

        assert result.allowed is False

    def test_owner_can_edit_any_block(self, validator):
        """Test owners can edit any block (with provenance)."""
        prov = Provenance(type=ProvenanceType.user_message, ref="test")
        for block_type in CoreBlockType:
            edit = create_edit(
                target=f"core:{block_type.value}",
                provenance=[prov],
            )
            result = validator.validate_edit(edit, editor="owner")

            assert result.allowed is True, f"Owner should be able to edit {block_type.value}"

    def test_is_block_agent_writable(self, validator):
        """Test checking if block is agent writable."""
        assert validator.is_block_agent_writable(CoreBlockType.mission) is True
        assert validator.is_block_agent_writable(CoreBlockType.human) is False


# ---------------------------------------------------------------------------
# Provenance Tests
# ---------------------------------------------------------------------------
class TestProvenance:
    """Tests for provenance validation."""

    def test_owner_block_requires_provenance(self, strict_validator):
        """Test owner-only blocks require provenance."""
        edit = create_edit(
            target="core:human",
            provenance=[],  # No provenance
        )
        result = strict_validator.validate_edit(edit, editor="owner")

        assert result.allowed is False
        assert "requires provenance" in result.reason

    def test_valid_provenance_accepted(self, strict_validator):
        """Test valid provenance is accepted."""
        prov = Provenance(
            type=ProvenanceType.user_message,
            ref="msg_123",
        )
        edit = create_edit(
            target="core:human",
            provenance=[prov],
        )
        result = strict_validator.validate_edit(edit, editor="owner")

        assert result.allowed is True

    def test_system_provenance_accepted(self, strict_validator):
        """Test system provenance is accepted."""
        prov = Provenance(
            type=ProvenanceType.system,
            ref="sys_init",
        )
        edit = create_edit(
            target="core:human",
            provenance=[prov],
        )
        result = strict_validator.validate_edit(edit, editor="owner")

        assert result.allowed is True

    def test_agent_inference_not_enough_for_owner_blocks(self, strict_validator):
        """Test agent inference alone isn't enough for owner blocks."""
        prov = Provenance(
            type=ProvenanceType.agent_inference,
            ref="inference_001",
        )
        edit = create_edit(
            target="core:human",
            provenance=[prov],
        )
        result = strict_validator.validate_edit(edit, editor="agent")

        assert result.allowed is False


# ---------------------------------------------------------------------------
# Secret Detection Tests
# ---------------------------------------------------------------------------
class TestSecretDetection:
    """Tests for secret detection and scrubbing."""

    def test_detect_api_key(self, validator):
        """Test detecting API key pattern."""
        edit = create_edit(
            target="core:mission",
            after="Use API_KEY=sk-abc123xyz789longkeyhere",
        )
        result = validator.validate_edit(edit, editor="agent")

        assert result.allowed is True
        assert result.scrubbed_content is not None
        assert "sk-abc123xyz" not in result.scrubbed_content
        assert "[REDACTED]" in result.scrubbed_content
        assert len(result.warnings) > 0

    def test_detect_bearer_token(self, validator):
        """Test detecting bearer token."""
        edit = create_edit(
            target="core:mission",
            after="Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        )
        result = validator.validate_edit(edit, editor="agent")

        assert result.scrubbed_content is not None
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result.scrubbed_content

    def test_detect_password(self, validator):
        """Test detecting password pattern."""
        edit = create_edit(
            target="working:item1",
            after="password=secretvalue123",
        )
        result = validator.validate_edit(edit, editor="agent")

        assert result.scrubbed_content is not None
        assert "secretvalue123" not in result.scrubbed_content

    def test_clean_content_not_scrubbed(self, validator):
        """Test clean content is not scrubbed."""
        edit = create_edit(
            target="core:mission",
            after="Complete the Python project by Friday",
        )
        result = validator.validate_edit(edit, editor="agent")

        assert result.scrubbed_content is None
        assert len(result.warnings) == 0

    def test_check_for_secrets(self, validator):
        """Test checking for secrets without scrubbing."""
        content_with_secrets = "API_KEY=abc123def456 and SECRET=mypassword"
        detected = validator.check_for_secrets(content_with_secrets)

        assert len(detected) > 0

        clean_content = "Just some normal text"
        detected = validator.check_for_secrets(clean_content)

        assert len(detected) == 0


# ---------------------------------------------------------------------------
# Confidence Threshold Tests
# ---------------------------------------------------------------------------
class TestConfidenceThreshold:
    """Tests for confidence threshold enforcement."""

    def test_low_confidence_rejected_for_core(self, strict_validator):
        """Test low confidence is rejected for core blocks."""
        edit = create_edit(
            target="core:mission",
            confidence=0.5,  # Below 0.8 threshold
        )
        result = strict_validator.validate_edit(edit, editor="agent")

        assert result.allowed is False
        assert "Confidence" in result.reason
        assert "below threshold" in result.reason

    def test_high_confidence_accepted(self, strict_validator):
        """Test high confidence is accepted."""
        edit = create_edit(
            target="core:mission",
            confidence=0.9,
        )
        result = strict_validator.validate_edit(edit, editor="agent")

        assert result.allowed is True

    def test_archival_threshold(self, strict_validator):
        """Test archival has different threshold."""
        edit = create_edit(
            target="archival:item1",
            confidence=0.4,  # Below 0.5 archival threshold
        )
        result = strict_validator.validate_edit(edit, editor="agent")

        assert result.allowed is False

        edit2 = create_edit(
            target="archival:item2",
            confidence=0.6,  # Above threshold
        )
        result2 = strict_validator.validate_edit(edit2, editor="agent")

        assert result2.allowed is True


# ---------------------------------------------------------------------------
# Quarantine Tests
# ---------------------------------------------------------------------------
class TestQuarantine:
    """Tests for quarantine-by-default logic."""

    def test_agent_edits_quarantined_by_default(self, strict_validator):
        """Test agent edits to core go to quarantine by default."""
        edit = create_edit(
            target="core:mission",
            confidence=0.9,
        )
        result = strict_validator.validate_edit(edit, editor="agent")

        assert result.allowed is True
        assert result.suggested_status == MemoryStatus.quarantined

    def test_owner_edits_not_quarantined(self, strict_validator):
        """Test owner edits don't go to quarantine."""
        prov = Provenance(type=ProvenanceType.user_message, ref="msg")
        edit = create_edit(
            target="core:human",
            provenance=[prov],
        )
        result = strict_validator.validate_edit(edit, editor="owner")

        assert result.allowed is True
        assert result.suggested_status == MemoryStatus.active

    def test_item_edits_not_quarantined(self, validator):
        """Test item edits don't go to quarantine by default."""
        edit = create_edit(target="working:item1")
        result = validator.validate_edit(edit, editor="agent")

        assert result.suggested_status != MemoryStatus.quarantined


# ---------------------------------------------------------------------------
# GateResult Tests
# ---------------------------------------------------------------------------
class TestGateResult:
    """Tests for GateResult dataclass."""

    def test_add_warning(self):
        """Test adding warnings to result."""
        result = GateResult(allowed=True, reason="OK")
        result.add_warning("Warning 1")
        result.add_warning("Warning 2")

        assert len(result.warnings) == 2
        assert "Warning 1" in result.warnings

    def test_default_status(self):
        """Test default suggested status."""
        result = GateResult(allowed=True, reason="OK")
        assert result.suggested_status == MemoryStatus.active


# ---------------------------------------------------------------------------
# Allowlist Summary Tests
# ---------------------------------------------------------------------------
class TestAllowlistSummary:
    """Tests for allowlist summary."""

    def test_get_allowlist_summary(self, validator):
        """Test getting allowlist summary."""
        summary = validator.get_allowlist_summary()

        assert "agent_writable" in summary
        assert "owner_only" in summary
        assert "all_blocks" in summary
        assert "mission" in summary["agent_writable"]
        assert "human" in summary["owner_only"]
