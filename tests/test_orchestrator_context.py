import hashlib
import hmac
from types import SimpleNamespace

from orchestrator_context import (
    init_context_cache,
    load_memory,
    log_rule_candidate,
    query_memory,
    update_rules,
)


class _ContextEnv:
    def __init__(self):
        self.reads = []

    def read_file(self, filename):
        self.reads.append(filename)
        return ""


def test_load_memory_reads_tier_a_files(tmp_path):
    runtime = SimpleNamespace(data_dir=str(tmp_path), context_env=_ContextEnv())
    (tmp_path / "RULES.md").write_text("", encoding="utf-8")

    load_memory(runtime)

    assert runtime.context_env.reads == [
        "USER.md",
        "RULES.md",
        "MEMORY_SUMMARY.md",
        "CAPABILITIES.md",
    ]


def test_load_memory_warns_when_rules_signature_mismatches(tmp_path, caplog):
    rules_path = tmp_path / "RULES.md"
    rules_path.write_text("trusted rules", encoding="utf-8")
    (tmp_path / "RULES.md.sig").write_text("bad-signature", encoding="utf-8")
    runtime = SimpleNamespace(data_dir=str(tmp_path), context_env=_ContextEnv())

    with caplog.at_level("WARNING"):
        load_memory(runtime)

    assert "HMAC signature mismatch for RULES.md" in caplog.text


def test_init_context_cache_disables_cache_for_non_gemini_provider():
    runtime = SimpleNamespace(
        provider=SimpleNamespace(provider_name="openai"),
        _cache=object(),
    )
    runtime.clear_context_cache = lambda: setattr(runtime, "_cache", None)

    init_context_cache(runtime)

    assert runtime._cache is None


def test_query_memory_returns_joined_documents():
    class MemoryCollection:
        def query(self, query_texts, n_results):
            assert query_texts == ["deployment"]
            assert n_results == 2
            return {"documents": [["doc one", "doc two"]]}

    runtime = SimpleNamespace(memory_collection=MemoryCollection())

    assert query_memory(runtime, "deployment", n_results=2) == "doc one\n- doc two"


def test_log_rule_candidate_appends_review_entry(tmp_path):
    runtime = SimpleNamespace(data_dir=str(tmp_path))

    log_rule_candidate(runtime, "- Candidate rule")

    assert "Candidate rule" in (tmp_path / "RULE_CANDIDATES.md").read_text(encoding="utf-8")


def test_update_rules_writes_signed_valid_rule_and_refreshes_cache(tmp_path):
    cache_refreshes = []
    runtime = SimpleNamespace(
        data_dir=str(tmp_path),
        rules_context="existing",
    )
    runtime.validate_rule_content = lambda content: (True, "")
    runtime.initialize_context_cache = lambda: cache_refreshes.append("refreshed")
    (tmp_path / "RULES.md").write_text("existing", encoding="utf-8")

    update_rules(runtime, "- Safe rule")

    rules_content = (tmp_path / "RULES.md").read_text(encoding="utf-8")
    stored_sig = (tmp_path / "RULES.md.sig").read_text(encoding="utf-8").strip()
    expected_sig = hmac.new(
        "default-dev-key".encode(),
        (tmp_path / "RULES.md").read_bytes(),
        hashlib.sha256,
    ).hexdigest()

    assert "- Safe rule" in rules_content
    assert runtime.rules_context == "existing\n- Safe rule"
    assert stored_sig == expected_sig
    assert cache_refreshes == ["refreshed"]


def test_update_rules_rejects_invalid_rule_without_writing(tmp_path):
    runtime = SimpleNamespace(
        data_dir=str(tmp_path),
        rules_context="existing",
    )
    runtime.validate_rule_content = lambda content: (False, "dangerous")
    runtime.initialize_context_cache = lambda: (_ for _ in ()).throw(AssertionError("cache should not refresh"))
    rules_path = tmp_path / "RULES.md"
    rules_path.write_text("existing", encoding="utf-8")

    update_rules(runtime, "dangerous")

    assert rules_path.read_text(encoding="utf-8") == "existing"
    assert not (tmp_path / "RULES.md.sig").exists()
