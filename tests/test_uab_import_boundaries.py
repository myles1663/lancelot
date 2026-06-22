"""Import-boundary tests for standalone-maintainable runtime spine modules."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = REPO_ROOT / "src" / "core"
SHARED_ROOT = REPO_ROOT / "src" / "shared"
UAB_DAEMON_ROOT = REPO_ROOT / "packages" / "uab" / "src"

APPROVED_UAB_ADAPTER_PATH = CORE_ROOT / "uab_runtime_adapter.py"
APPROVED_UAB_ADAPTER = "src.core.uab_runtime_adapter"
FORBIDDEN_UAB_IMPORTS = {
    "src.tools.providers.uab_bridge",
    "tools.providers.uab_bridge",
    "packages.uab",
}
CORE_ENTRY_PATTERNS = ("gateway*.py", "boot*.py", "orchestrator*.py")

RECEIPT_BOUNDARY_FORBIDDEN_IMPORTS = {
    "src.core.gateway",
    "src.ui",
    "src.warroom",
    "warroom",
}
MEMORY_BOUNDARY_FORBIDDEN_IMPORTS = {
    "src.core.orchestrator",
    "core.orchestrator",
}
GOVERNANCE_MODEL_BOUNDARY_FORBIDDEN_IMPORTS = (
    FORBIDDEN_UAB_IMPORTS
    | {
        "src.core.gateway",
        "src.core.orchestrator",
        "src.core.uab_runtime_adapter",
        "src.ui",
        "src.warroom",
        "warroom",
    }
)
GOVERNANCE_MODEL_FILES = (
    CORE_ROOT / "governance" / "models.py",
    CORE_ROOT / "governance" / "trust_models.py",
    CORE_ROOT / "governance" / "approval_learning" / "models.py",
)
UAB_DAEMON_POLICY_MARKERS = (
    "src.core.governance",
    "governance.models",
    "governance.approval_learning",
    "DecisionContext.from_action",
    "RiskClassification.from_tier",
    "RiskTier.T",
)
UAB_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".cjs"}


def _display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _core_entry_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in CORE_ENTRY_PATTERNS:
        files.update(CORE_ROOT.glob(pattern))
    return sorted(files)


def _receipt_files() -> list[Path]:
    return sorted(SHARED_ROOT.glob("*receipt*.py"))


def _memory_persistence_files() -> list[Path]:
    return sorted((CORE_ROOT / "memory").glob("*.py"))


def _core_python_files() -> list[Path]:
    return sorted(CORE_ROOT.rglob("*.py"))


def _uab_daemon_source_files() -> list[Path]:
    if not UAB_DAEMON_ROOT.exists():
        return []
    return sorted(
        path
        for path in UAB_DAEMON_ROOT.rglob("*")
        if path.is_file() and path.suffix in UAB_SOURCE_SUFFIXES
    )


def _module_name_for_path(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    return ".".join(relative.parts)


def _resolve_import_from_module(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""

    package_parts = _module_name_for_path(path).split(".")[:-1]
    if node.level > len(package_parts):
        return node.module or ""
    base_parts = package_parts[: len(package_parts) - node.level + 1]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(part for part in base_parts if part)


def _imported_modules_from_text(source: str, path: Path) -> set[str]:
    tree = ast.parse(source, filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(path, node)
            if module:
                imported.add(module)
                imported.update(f"{module}.{alias.name}" for alias in node.names)
    return imported


def _imported_modules(path: Path) -> set[str]:
    return _imported_modules_from_text(path.read_text(encoding="utf-8"), path)


def _module_matches(imported: str, forbidden_module: str) -> bool:
    return imported == forbidden_module or imported.startswith(f"{forbidden_module}.")


def _forbidden_imports(imports: set[str], forbidden_modules: set[str]) -> list[str]:
    forbidden: set[str] = set()
    for imported in imports:
        for forbidden_module in forbidden_modules:
            if _module_matches(imported, forbidden_module):
                forbidden.add(imported)
    return sorted(forbidden)


def _violations_for_paths(paths: list[Path], forbidden_modules: set[str]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        forbidden = _forbidden_imports(_imported_modules(path), forbidden_modules)
        if forbidden:
            violations.append(f"{_display(path)}: {', '.join(forbidden)}")
    return violations


def _text_marker_violations(paths: list[Path], forbidden_markers: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        matched = sorted(marker for marker in forbidden_markers if marker in text)
        if matched:
            violations.append(f"{_display(path)}: {', '.join(matched)}")
    return violations


def test_core_uab_integration_uses_runtime_adapter_boundary():
    violations: list[str] = []
    adapter_users: list[str] = []
    for path in _core_entry_files():
        if path == APPROVED_UAB_ADAPTER_PATH:
            continue
        imports = _imported_modules(path)
        forbidden = _forbidden_imports(imports, FORBIDDEN_UAB_IMPORTS)
        if forbidden:
            violations.append(f"{_display(path)}: {', '.join(forbidden)}")
        if APPROVED_UAB_ADAPTER in imports:
            adapter_users.append(_display(path))

    assert violations == []
    assert adapter_users, "Core UAB integration should use the approved runtime adapter"


def test_only_approved_core_adapter_imports_uab_provider_internals():
    adapter_imports = _imported_modules(APPROVED_UAB_ADAPTER_PATH)
    other_core_files = [
        path for path in _core_python_files() if path != APPROVED_UAB_ADAPTER_PATH
    ]

    assert "src.tools.providers.uab_bridge" in adapter_imports
    assert _violations_for_paths(other_core_files, FORBIDDEN_UAB_IMPORTS) == []


def test_receipt_modules_do_not_import_gateway_or_ui_surfaces():
    assert _violations_for_paths(_receipt_files(), RECEIPT_BOUNDARY_FORBIDDEN_IMPORTS) == []


def test_memory_persistence_does_not_import_orchestrator_control_flow():
    assert _violations_for_paths(_memory_persistence_files(), MEMORY_BOUNDARY_FORBIDDEN_IMPORTS) == []


def test_governance_models_do_not_import_runtime_or_uab_surfaces():
    assert _violations_for_paths(list(GOVERNANCE_MODEL_FILES), GOVERNANCE_MODEL_BOUNDARY_FORBIDDEN_IMPORTS) == []


def test_uab_daemon_does_not_import_python_policy_decision_logic():
    assert _text_marker_violations(
        _uab_daemon_source_files(),
        UAB_DAEMON_POLICY_MARKERS,
    ) == []


def test_import_boundary_detects_parent_module_alias_escape():
    imports = {"src.tools.providers", "src.tools.providers.uab_bridge"}

    assert _forbidden_imports(imports, FORBIDDEN_UAB_IMPORTS) == ["src.tools.providers.uab_bridge"]


def test_import_parser_canonicalizes_relative_uab_provider_imports():
    path = REPO_ROOT / "src" / "core" / "gateway_example.py"
    imports = _imported_modules_from_text(
        "from ..tools.providers import uab_bridge\n",
        path,
    )

    assert "src.tools.providers.uab_bridge" in imports
    assert _forbidden_imports(imports, FORBIDDEN_UAB_IMPORTS) == [
        "src.tools.providers.uab_bridge"
    ]


def test_import_parser_canonicalizes_relative_memory_orchestrator_imports():
    path = REPO_ROOT / "src" / "core" / "memory" / "sqlite_store.py"
    imports = _imported_modules_from_text(
        "from .. import orchestrator\nfrom ..orchestrator import LancelotOrchestrator\n",
        path,
    )

    assert "src.core.orchestrator" in imports
    assert _forbidden_imports(imports, MEMORY_BOUNDARY_FORBIDDEN_IMPORTS) == [
        "src.core.orchestrator",
        "src.core.orchestrator.LancelotOrchestrator",
    ]


def test_import_parser_canonicalizes_relative_receipt_gateway_imports():
    path = REPO_ROOT / "src" / "shared" / "receipts_store.py"
    imports = _imported_modules_from_text(
        "from ..core import gateway\nfrom ..core.gateway import app\n",
        path,
    )

    assert "src.core.gateway" in imports
    assert _forbidden_imports(imports, RECEIPT_BOUNDARY_FORBIDDEN_IMPORTS) == [
        "src.core.gateway",
        "src.core.gateway.app",
    ]


def test_import_parser_canonicalizes_relative_governance_model_runtime_imports():
    path = REPO_ROOT / "src" / "core" / "governance" / "models.py"
    imports = _imported_modules_from_text(
        "from .. import orchestrator\nfrom ..uab_runtime_adapter import register_uab_provider\n",
        path,
    )

    assert "src.core.orchestrator" in imports
    assert "src.core.uab_runtime_adapter" in imports
    assert _forbidden_imports(imports, GOVERNANCE_MODEL_BOUNDARY_FORBIDDEN_IMPORTS) == [
        "src.core.orchestrator",
        "src.core.uab_runtime_adapter",
        "src.core.uab_runtime_adapter.register_uab_provider",
    ]


def test_import_boundary_does_not_reject_sibling_prefixes():
    imports = {"src.core.orchestrator_context", "src.core.orchestrator_execution"}

    assert _forbidden_imports(imports, MEMORY_BOUNDARY_FORBIDDEN_IMPORTS) == []


def test_text_marker_boundary_detects_uab_daemon_policy_leak(tmp_path: Path):
    leaking_daemon_file = tmp_path / "daemon.ts"
    leaking_daemon_file.write_text(
        "const tier = 'RiskTier.T3_IRREVERSIBLE';\n",
        encoding="utf-8",
    )

    assert _text_marker_violations([leaking_daemon_file], UAB_DAEMON_POLICY_MARKERS) == [
        f"{leaking_daemon_file.as_posix()}: RiskTier.T"
    ]
