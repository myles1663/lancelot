"""
Shared Workspace Connector — configures the shared folder between host and container.

This is a config-only connector with no API operations. It stores the host
folder path in the vault and manages the Docker volume mount in docker-compose.yml.
When the path is changed, the container auto-restarts with the new mount.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec


class SharedWorkspaceConnector(ConnectorBase):
    """Shared Workspace connector — configure the host folder path."""

    def __init__(self, vault=None) -> None:
        manifest = ConnectorManifest(
            id="shared_workspace",
            name="Shared Workspace",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description=(
                "Configure the shared folder between your computer and Lancelot. "
                "Documents, reports, and exports are written here. "
                "Enter the full path to a folder on your computer (e.g. "
                "C:\\Users\\You\\Desktop\\Lancelot Workspace)."
            ),
            target_domains=["localhost"],
            required_credentials=[
                CredentialSpec(
                    name="Host Folder Path",
                    type="config",
                    vault_key="shared_workspace.host_path",
                    required=True,
                ),
            ],
            data_reads=["Files from shared folder"],
            data_writes=["Documents, reports, exports to shared folder"],
            does_not_access=["Files outside the configured folder"],
        )
        super().__init__(manifest)
        self._vault = vault

    def get_operations(self) -> List:
        """No API operations — this is a config-only connector."""
        return []

    def execute(self, operation_id: str, params: dict) -> Any:
        raise NotImplementedError("SharedWorkspaceConnector has no executable operations")

    def validate_credentials(self) -> bool:
        """Check that the workspace mount exists inside the container."""
        workspace = Path("/home/lancelot/workspace")
        return workspace.exists() and workspace.is_dir()
