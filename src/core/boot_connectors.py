"""Connector runtime initialization for gateway boot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConnectorBootState:
    vault: object | None = None
    error: str | None = None


def init_connector_runtime(app, *, main_orchestrator, boot_vault, logger) -> ConnectorBootState:
    """Mount connector APIs, register configured connectors, and expose runtime state."""
    try:
        from connectors.base import ConnectorStatus
        from connectors.credential_api import init_credential_api, router as cred_router
        from connectors.registry import ConnectorRegistry
        from connectors.runtime import ConnectorRuntime
        from connectors.vault import CredentialVault as ConnectorVault
        from connectors_api import init_connectors_api, router as connectors_mgmt_router

        connector_registry = ConnectorRegistry(config_path="config/connectors.yaml")
        connector_vault = boot_vault if boot_vault else ConnectorVault(config_path="config/vault.yaml")

        from feature_flags import FEATURE_CONNECTORS

        if FEATURE_CONNECTORS:
            connector_configs = getattr(connector_registry, "connector_configurations", None)
            connector_config = (
                connector_configs()
                if callable(connector_configs)
                else getattr(connector_registry, "_config", {}).get("connectors", {})
            )
            for connector_id, connector_cfg in connector_config.items():
                if connector_cfg.get("enabled", False):
                    try:
                        from connectors_api import register_connector_with_vault_access
                        from src.connectors.google_feature_gate import (
                            google_connector_disabled_reason,
                            is_google_connector_enabled,
                        )

                        backend = connector_cfg.get("backend")
                        if not is_google_connector_enabled(connector_id, backend):
                            logger.info(
                                "Skipping connector %s registration: %s",
                                connector_id,
                                google_connector_disabled_reason(connector_id, backend),
                            )
                            continue

                        connector = register_connector_with_vault_access(
                            connector_registry,
                            connector_vault,
                            connector_id,
                            connector_cfg,
                        )
                        if connector:
                            if connector.status == ConnectorStatus.CONFIGURED:
                                logger.info("Connector registered + configured: %s", connector_id)
                            else:
                                logger.info("Connector registered but pending credentials: %s", connector_id)
                    except Exception as exc:
                        logger.warning("Failed to register connector %s: %s", connector_id, exc)

        connector_policy_engine = None
        try:
            from src.tools.fabric import get_tool_fabric
            connector_policy_engine = getattr(get_tool_fabric(), "_policy_engine", None)
        except Exception as exc:
            logger.debug("Connector policy engine unavailable: %s", exc)

        init_credential_api(connector_registry, connector_vault)
        init_connectors_api(connector_registry, connector_vault)
        app.include_router(cred_router)
        app.include_router(connectors_mgmt_router)

        try:
            connector_runtime = ConnectorRuntime(
                registry=connector_registry,
                vault=connector_vault,
                risk_classifier=getattr(main_orchestrator, "_risk_classifier", None),
                policy_engine=connector_policy_engine,
                receipt_service=getattr(main_orchestrator, "receipt_service", None),
                trust_ledger=getattr(main_orchestrator, "trust_ledger", None),
            )
            for entry in connector_registry.list_connectors():
                connector_runtime.register_connector(entry.manifest.id)

            main_orchestrator.connector_runtime = connector_runtime
            if getattr(main_orchestrator, "task_runner", None) is not None:
                main_orchestrator.task_runner.connector_runtime = connector_runtime
            app.state.connector_runtime = connector_runtime
        except Exception as exc:
            logger.warning("Connector runtime degraded: %s", exc)

        main_orchestrator.attach_connector_registry(connector_registry)

        if not connector_vault.exists("shared_workspace.host_path"):
            try:
                import re

                compose_file = Path("/home/lancelot/app/docker-compose.yml")
                if compose_file.exists():
                    compose_text = compose_file.read_text(encoding="utf-8")
                    workspace_match = re.search(
                        r'-\s*["\']?(.+?):/home/lancelot/workspace',
                        compose_text,
                    )
                    if workspace_match:
                        workspace_path = workspace_match.group(1).strip().strip('"').strip("'")
                        connector_vault.store("shared_workspace.host_path", workspace_path, type="config")
                        logger.info("Seeded vault with workspace path: %s", workspace_path)
            except Exception as exc:
                logger.debug("Could not seed workspace path: %s", exc)

        logger.info("Connectors subsystem initialized (FEATURE_CONNECTORS=%s).", FEATURE_CONNECTORS)
        return ConnectorBootState(vault=connector_vault)
    except Exception as exc:
        logger.warning("Connectors initialization failed: %s", exc)
        return ConnectorBootState(error=str(exc))
