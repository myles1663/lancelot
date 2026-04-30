"""
Tests for Prompts 48-49: SkillManifest Schema + Advanced Validation.
"""

import pytest
from pydantic import ValidationError
from src.skills.security.manifest import (
    SkillManifest,
    SkillCapabilityDeclaration,
    SkillCredentialDeclaration,
    validate_manifest,
)


def _valid_manifest(**overrides):
    """Create a valid manifest dict with optional overrides."""
    defaults = {
        "id": "slack-summarizer",
        "name": "Slack Summarizer",
        "version": "1.0.0",
        "author": "lancelot",
        "source": "first-party",
        "description": "Summarizes Slack channels",
        "capabilities_required": [
            {"capability": "connector.read", "description": "Read Slack messages"},
        ],
        "capabilities_optional": [
            {"capability": "connector.write", "description": "Post summaries"},
        ],
        "credentials": [
            {"vault_key": "slack.bot_token", "type": "bearer", "purpose": "API access"},
        ],
        "target_domains": ["slack.com"],
        "data_reads": ["Slack messages"],
        "data_writes": ["Summary posts"],
        "does_not_access": ["DMs", "Private channels"],
    }
    defaults.update(overrides)
    return defaults


# ── Prompt 48: Basic Validation ──────────────────────────────────

class TestBasicValidation:
    def test_valid_manifest_parses(self):
        m = validate_manifest(_valid_manifest())
        assert m.id == "slack-summarizer"

    def test_empty_id_error(self):
        with pytest.raises(ValidationError, match="empty"):
            validate_manifest(_valid_manifest(id=""))

    def test_invalid_source_error(self):
        with pytest.raises(ValidationError, match="source"):
            validate_manifest(_valid_manifest(source="malicious"))

    def test_empty_capabilities_error(self):
        with pytest.raises(ValidationError, match="capability"):
            validate_manifest(_valid_manifest(capabilities_required=[]))

    def test_wildcard_domain_error(self):
        with pytest.raises(ValidationError, match="Wildcard"):
            validate_manifest(_valid_manifest(target_domains=["*.evil.com"]))

    def test_all_capabilities_combines(self):
        m = validate_manifest(_valid_manifest())
        caps = m.all_capabilities()
        assert "connector.read" in caps
        assert "connector.write" in caps
        assert len(caps) == 2

    def test_all_vault_keys(self):
        m = validate_manifest(_valid_manifest())
        keys = m.all_vault_keys()
        assert keys == ["slack.bot_token"]

    def test_optional_fields_empty_ok(self):
        m = validate_manifest(_valid_manifest(
            capabilities_optional=[],
            credentials=[],
            target_domains=[],
            data_reads=[],
            data_writes=[],
        ))
        assert m.id == "slack-summarizer"

    def test_validate_manifest_valid_dict(self):
        m = validate_manifest(_valid_manifest())
        assert isinstance(m, SkillManifest)

    def test_validate_manifest_invalid_raises(self):
        with pytest.raises(ValidationError):
            validate_manifest({"id": "", "name": "x", "version": "1", "author": "a",
                               "source": "user", "capabilities_required": []})


# ── Prompt 49: Advanced Validation ───────────────────────────────

class TestAdvancedValidation:
    def test_credentials_without_domains_error(self):
        with pytest.raises(ValidationError, match="target domains"):
            validate_manifest(_valid_manifest(
                target_domains=[],
                credentials=[{"vault_key": "k", "type": "t", "purpose": "p"}],
            ))

    def test_community_empty_does_not_access_error(self):
        with pytest.raises(ValidationError, match="does_not_access"):
            validate_manifest(_valid_manifest(source="community", does_not_access=[]))

    def test_first_party_empty_does_not_access_ok(self):
        m = validate_manifest(_valid_manifest(source="first-party", does_not_access=[]))
        assert m.source == "first-party"


class TestAudit:
    def test_clean_manifest_no_findings(self):
        m = validate_manifest(_valid_manifest())
        findings = m.audit()
        # Only "info" for optional capabilities
        errors = [f for f in findings if f["level"] == "error"]
        assert len(errors) == 0

    def test_writes_without_does_not_access(self):
        m = validate_manifest(_valid_manifest(
            source="first-party",
            does_not_access=[],
            capabilities_required=[
                {"capability": "connector.write", "description": "Write stuff"},
            ],
        ))
        findings = m.audit()
        warnings = [f for f in findings if f["level"] == "warning"]
        assert any("does_not_access" in w["message"] for w in warnings)

    def test_too_many_domains(self):
        m = validate_manifest(_valid_manifest(
            target_domains=["a.com", "b.com", "c.com", "d.com", "e.com", "f.com"],
        ))
        findings = m.audit()
        warnings = [f for f in findings if f["level"] == "warning"]
        assert any("broad" in w["message"].lower() for w in warnings)

    def test_unrecognized_capability_error(self):
        m = validate_manifest(_valid_manifest(
            capabilities_required=[
                {"capability": "magic.wand", "description": "Cast spells"},
            ],
            does_not_access=["Nothing"],
        ))
        findings = m.audit()
        errors = [f for f in findings if f["level"] == "error"]
        assert any("Unrecognized" in e["message"] for e in errors)
