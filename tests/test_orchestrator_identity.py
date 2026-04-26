from types import SimpleNamespace

from orchestrator_identity import build_execution_instruction, build_self_awareness


def test_build_self_awareness_describes_lancelot_specific_systems():
    text = build_self_awareness()

    assert "You ARE Lancelot" in text
    assert "Governed Autonomous System" in text
    assert "CAPABILITIES.md" in text
    assert "as a language model" in text
    assert "generic AI" in text


def test_build_execution_instruction_uses_default_persona_without_soul():
    runtime = SimpleNamespace(soul=None)

    instruction = build_execution_instruction(runtime)

    assert "You are Lancelot, a governed autonomous assistant." in instruction
    assert "EXECUTION MODE" in instruction
    assert "The user has reviewed and approved a plan" in instruction
    assert "refuse to bypass security checks or permission controls" in instruction


def test_build_execution_instruction_uses_soul_identity():
    runtime = SimpleNamespace(
        soul=SimpleNamespace(
            mission="protect governed execution",
            allegiance="Commander",
            tone_invariants=["precise", "direct"],
        )
    )

    instruction = build_execution_instruction(runtime)

    assert "Mission: protect governed execution" in instruction
    assert "Allegiance: Commander" in instruction
    assert "Tone: precise, direct" in instruction


def test_build_execution_instruction_includes_host_bridge_warning(monkeypatch):
    import src.core.feature_flags as feature_flags

    monkeypatch.setattr(feature_flags, "FEATURE_TOOLS_HOST_BRIDGE", True)

    instruction = build_execution_instruction(SimpleNamespace(soul=None))

    assert "HOST OS ACCESS (ACTIVE)" in instruction
    assert "REAL WINDOWS HOST MACHINE" in instruction
    assert "Never use Linux commands" in instruction


def test_build_execution_instruction_applies_crusader_overlay(monkeypatch):
    calls = []

    def modify_prompt(prompt):
        calls.append(prompt)
        return f"modified::{prompt}"

    monkeypatch.setenv("CRUSADER_MODE", "true")
    monkeypatch.setattr("crusader.CrusaderPromptModifier.modify_prompt", modify_prompt)

    instruction = build_execution_instruction(SimpleNamespace(soul=None))

    assert instruction.startswith("modified::")
    assert calls
