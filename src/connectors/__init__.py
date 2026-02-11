"""
Lancelot Capability Upgrade: External Connectors

Governed integrations with external systems. Connectors declare capabilities
via manifests and route all operations through ConnectorProxy → PolicyEngine.
Connectors NEVER make network calls directly.
"""
__version__ = "0.1.0"
