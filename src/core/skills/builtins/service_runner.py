"""
Built-in skill: service_runner — manage Docker services and run health checks.

Supports docker compose up/down and HTTP/TCP health checks.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, Dict
from urllib.request import urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)

# Skill manifest metadata
MANIFEST = {
    "name": "service_runner",
    "version": "1.0.0",
    "description": "Manage Docker services and run health checks",
    "risk": "HIGH",
    "permissions": ["service_manage"],
    "inputs": [
        {"name": "action", "type": "string", "required": True,
         "description": "up|down|health|status"},
        {"name": "service", "type": "string", "required": False,
         "description": "Service name (for up/down)"},
        {"name": "health_url", "type": "string", "required": False,
         "description": "URL to check for health action"},
        {"name": "compose_file", "type": "string", "required": False,
         "description": "Path to docker-compose file"},
    ],
}

DEFAULT_TIMEOUT = 10


def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a service management action.

    Args:
        context: SkillContext
        inputs: Dict with 'action' and action-specific params

    Returns:
        Dict with action results
    """
    action = inputs.get("action", "").lower()

    if action == "up":
        return _docker_up(inputs)
    elif action == "down":
        return _docker_down(inputs)
    elif action == "health":
        return _health_check(inputs)
    elif action == "status":
        return _docker_status(inputs)
    else:
        raise ValueError(f"Unknown action: '{action}'. Must be up|down|health|status")


def _docker_up(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Start a Docker service."""
    service = inputs.get("service", "")
    compose_file = inputs.get("compose_file", "docker-compose.yml")

    cmd = ["docker", "compose", "-f", compose_file, "up", "-d"]
    if service:
        cmd.append(service)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        logger.info("service_runner: docker up %s (rc=%d)", service or "all", result.returncode)
        return {
            "status": "started" if result.returncode == 0 else "error",
            "service": service or "all",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        raise TimeoutError("Docker compose up timed out after 120s")


def _docker_down(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Stop a Docker service."""
    service = inputs.get("service", "")
    compose_file = inputs.get("compose_file", "docker-compose.yml")

    cmd = ["docker", "compose", "-f", compose_file, "down"]
    if service:
        cmd = ["docker", "compose", "-f", compose_file, "stop", service]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        logger.info("service_runner: docker down %s (rc=%d)", service or "all", result.returncode)
        return {
            "status": "stopped" if result.returncode == 0 else "error",
            "service": service or "all",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        raise TimeoutError("Docker compose down timed out after 60s")


def _health_check(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Run an HTTP health check against a URL."""
    url = inputs.get("health_url", "")
    timeout = inputs.get("timeout_sec", DEFAULT_TIMEOUT)

    if not url:
        raise ValueError("Missing required input: 'health_url'")

    start = time.monotonic()
    try:
        response = urlopen(url, timeout=timeout)
        duration_ms = (time.monotonic() - start) * 1000
        status_code = response.getcode()
        body = response.read(1024).decode("utf-8", errors="replace")

        healthy = 200 <= status_code < 400
        logger.info("service_runner: health check %s -> %d (%.1fms)",
                     url, status_code, duration_ms)

        return {
            "status": "healthy" if healthy else "unhealthy",
            "status_code": status_code,
            "response_body": body[:500],
            "duration_ms": round(duration_ms, 2),
            "url": url,
        }
    except (URLError, OSError) as e:
        duration_ms = (time.monotonic() - start) * 1000
        return {
            "status": "unreachable",
            "error": str(e),
            "duration_ms": round(duration_ms, 2),
            "url": url,
        }


def _docker_status(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Get Docker container status."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"],
            capture_output=True, text=True, timeout=10,
        )
        containers = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                containers.append({
                    "name": parts[0],
                    "status": parts[1],
                    "ports": parts[2] if len(parts) > 2 else "",
                })
        return {"status": "ok", "containers": containers}
    except Exception as e:
        return {"status": "error", "error": str(e)}
