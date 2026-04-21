"""
Setup & Recovery API — /api/setup/*

System administration endpoints for the War Room Setup & Recovery page.
Container controls, log viewer, vault management, config reload,
export/backup, and danger zone operations.

All destructive operations are audit-logged and require {"confirm": true}.
"""

import io
import json
import logging
import os
import platform
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from src.core.auth_api import require_operator_capability
from update_checker import read_current_version

logger = logging.getLogger(__name__)

# Set by init_setup_api() at startup
_data_dir: Optional[Path] = None
_startup_time: Optional[float] = None
_audit_logger = None
_connector_vault = None
_connector_vault_error: Optional[str] = None
_connector_vault_config_path = "config/vault.yaml"
_receipt_service = None
_verify_request = None


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = Field(default=False)


class FactoryResetRequest(ConfirmRequest):
    confirmation_text: str | None = Field(default=None)


class VaultResetRequest(ConfirmRequest):
    confirmation_text: str | None = Field(default=None)


def _require_authenticated_request(request: Request) -> None:
    """Fail closed unless gateway auth verification is explicitly wired in."""
    if _verify_request is None:
        logger.error("Setup API auth callback not configured; refusing request")
        raise HTTPException(status_code=503, detail="Setup API auth not configured")
    if not _verify_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


router = APIRouter(
    prefix="/api/setup",
    tags=["setup"],
    dependencies=[
        Depends(_require_authenticated_request),
        Depends(require_operator_capability("setup.admin")),
    ],
)


def init_setup_api(
    data_dir: str,
    startup_time: float,
    audit_logger=None,
    connector_vault=None,
    connector_vault_error: str | None = None,
    connector_vault_config_path: str = "config/vault.yaml",
    receipt_service=None,
    verify_request=None,
) -> None:
    """Initialise the setup API with references to subsystems."""
    global _data_dir, _startup_time, _audit_logger, _connector_vault, _connector_vault_error
    global _connector_vault_config_path, _receipt_service, _verify_request
    _data_dir = Path(data_dir)
    _startup_time = startup_time
    _audit_logger = audit_logger
    _connector_vault = connector_vault
    _connector_vault_error = connector_vault_error
    _connector_vault_config_path = connector_vault_config_path
    _receipt_service = receipt_service
    _verify_request = verify_request
    logger.info("Setup API initialised (data_dir=%s)", data_dir)


def _safe_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message, "status": status_code})


def _resolve_audit_user(request: Optional[Request]) -> str:
    """Resolve the authenticated operator for audit logging."""
    if request is None:
        return "WarRoom"
    try:
        from src.core.auth_api import get_api_key_identity, resolve_operator_identity
    except ImportError:
        return "WarRoom"

    try:
        identity = resolve_operator_identity(request) or get_api_key_identity(request)
    except Exception as exc:
        logger.warning("Failed to resolve setup audit identity: %s", exc)
        return "WarRoom"

    if not identity:
        return "WarRoom"
    return identity.display_name or identity.operator_id or "WarRoom"


def _audit(event_type: str, details: str, request: Optional[Request] = None) -> None:
    """Log to audit trail if available."""
    if _audit_logger:
        try:
            _audit_logger.log_event(event_type, details, user=_resolve_audit_user(request))
        except Exception as exc:
            logger.warning("Failed to write setup audit event %s: %s", event_type, exc)


def _connector_vault_health() -> dict:
    from src.connectors.vault import CredentialVault

    if _connector_vault is not None and hasattr(_connector_vault, "health_snapshot"):
        return _connector_vault.health_snapshot(last_error=_connector_vault_error).to_dict()

    return CredentialVault.inspect_health(
        config_path=_connector_vault_config_path,
        last_error=_connector_vault_error,
    ).to_dict()


# ------------------------------------------------------------------
# System Info
# ------------------------------------------------------------------

@router.get("/system-info")
async def system_info():
    """Version, uptime, Python version, platform, disk usage."""
    try:
        uptime = round(time.time() - _startup_time, 1) if _startup_time else 0
        data_dir_info = {"path": str(_data_dir), "total_mb": 0, "used_mb": 0}
        degraded_reasons: list[str] = []
        runtime_errors: list[str] = []
        if _data_dir and _data_dir.exists():
            try:
                usage = shutil.disk_usage(str(_data_dir))
                data_dir_info["total_mb"] = round(usage.total / (1024 * 1024), 1)
                data_dir_info["used_mb"] = round(usage.used / (1024 * 1024), 1)
            except Exception as exc:
                logger.warning("Setup system-info disk usage lookup failed: %s", exc)
                degraded_reasons.append("Disk usage unavailable")
                runtime_errors.append(str(exc))

        hostname = ""
        try:
            hostname = os.environ.get("HOSTNAME", platform.node())
        except Exception as exc:
            logger.warning("Setup system-info hostname lookup failed: %s", exc)
            degraded_reasons.append("Hostname unavailable")
            runtime_errors.append(str(exc))

        return {
            "version": read_current_version(),
            "uptime_seconds": uptime,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "hostname": hostname,
            "data_dir": data_dir_info,
            "runtime_degraded": bool(degraded_reasons),
            "degraded_reasons": degraded_reasons,
            "runtime_errors": runtime_errors,
        }
    except Exception as exc:
        logger.error("system_info error: %s", exc)
        return _safe_error(500, "Failed to retrieve system info")


# ------------------------------------------------------------------
# Container Controls
# ------------------------------------------------------------------

@router.post("/restart")
async def restart_container(request: Request, body: ConfirmRequest):
    """Graceful restart — os._exit(0) so Docker restarts the container."""
    try:
        if not body.confirm:
            return _safe_error(400, "Confirmation required: {\"confirm\": true}")

        _audit("SETUP_RESTART", "Container restart initiated via War Room", request=request)
        logger.warning("RESTART initiated via Setup API — exiting with code 0")

        # Flush state before exit
        degraded_reasons: list[str] = []
        runtime_errors: list[str] = []
        try:
            from subsystem_manager import subsystem_manager
            subsystem_manager.stop_all()
        except Exception as exc:
            logger.warning("Subsystem stop_all failed during restart: %s", exc)
            degraded_reasons.append("Subsystem shutdown incomplete before restart")
            runtime_errors.append(str(exc))

        # Schedule exit after response is sent
        import threading
        threading.Timer(0.5, lambda: os._exit(0)).start()

        return {
            "status": "restarting",
            "message": "Container will restart momentarily",
            "runtime_degraded": bool(degraded_reasons),
            "degraded_reasons": degraded_reasons,
            "runtime_errors": runtime_errors,
        }
    except Exception as exc:
        logger.error("restart error: %s", exc)
        return _safe_error(500, "Failed to initiate restart")


@router.post("/shutdown")
async def shutdown_container(request: Request, body: ConfirmRequest):
    """Graceful shutdown — os._exit(1) so Docker does NOT restart."""
    try:
        if not body.confirm:
            return _safe_error(400, "Confirmation required: {\"confirm\": true}")

        _audit("SETUP_SHUTDOWN", "Container shutdown initiated via War Room", request=request)
        logger.warning("SHUTDOWN initiated via Setup API — exiting with code 1")

        degraded_reasons: list[str] = []
        runtime_errors: list[str] = []
        try:
            from subsystem_manager import subsystem_manager
            subsystem_manager.stop_all()
        except Exception as exc:
            logger.warning("Subsystem stop_all failed during shutdown: %s", exc)
            degraded_reasons.append("Subsystem shutdown incomplete before container stop")
            runtime_errors.append(str(exc))

        import threading
        threading.Timer(0.5, lambda: os._exit(1)).start()

        return {
            "status": "shutting_down",
            "message": "Container will shut down momentarily (will not auto-restart)",
            "runtime_degraded": bool(degraded_reasons),
            "degraded_reasons": degraded_reasons,
            "runtime_errors": runtime_errors,
        }
    except Exception as exc:
        logger.error("shutdown error: %s", exc)
        return _safe_error(500, "Failed to initiate shutdown")


# ------------------------------------------------------------------
# Log Viewer
# ------------------------------------------------------------------

@router.get("/logs")
async def get_logs(
    lines: int = Query(200, ge=1, le=2000),
    file: str = Query("audit"),
):
    """Read last N lines from audit.log or vault access.log."""
    try:
        if file == "audit":
            log_path = _data_dir / "audit.log" if _data_dir else Path("/home/lancelot/data/audit.log")
        elif file == "vault":
            log_path = Path("data/vault/access.log")
        else:
            return _safe_error(400, f"Unknown log file: {file}. Use 'audit' or 'vault'.")

        if not log_path.exists():
            return {"lines": [], "file": file, "total_lines": 0}

        all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return {"lines": tail, "file": file, "total_lines": len(all_lines)}
    except Exception as exc:
        logger.error("get_logs error: %s", exc)
        return _safe_error(500, "Failed to read logs")


# ------------------------------------------------------------------
# Vault Management
# ------------------------------------------------------------------

@router.get("/vault/status")
async def vault_status():
    """Return non-secret connector-vault diagnostics for recovery flows."""
    try:
        return _connector_vault_health()
    except Exception as exc:
        logger.error("vault_status error: %s", exc)
        return _safe_error(500, "Failed to inspect connector vault status")

@router.get("/vault/keys")
async def list_vault_keys():
    """List all credential keys (never values) with metadata."""
    try:
        if _connector_vault is None:
            return {"keys": [], "message": "Vault not initialised"}

        keys = _connector_vault.list_keys()
        entries = []
        for key in keys:
            entry = _connector_vault._entries.get(key)
            entries.append({
                "key": key,
                "type": entry.type if entry else "unknown",
                "created_at": entry.created_at if entry else "",
            })

        return {"keys": entries, "total": len(entries)}
    except Exception as exc:
        logger.error("list_vault_keys error: %s", exc)
        return _safe_error(500, "Failed to list vault keys")


@router.get("/vault/masked")
async def list_vault_masked():
    """List all credential keys with masked values."""
    try:
        if _connector_vault is None:
            return {"keys": [], "message": "Vault not initialised"}

        keys = _connector_vault.list_keys()
        entries = []
        for key in keys:
            entry = _connector_vault._entries.get(key)
            raw_val = _connector_vault.retrieve(key) if _connector_vault else None
            masked = _mask_value(raw_val) if raw_val else "••••"
            entries.append({
                "key": key,
                "type": entry.type if entry else "unknown",
                "created_at": entry.created_at if entry else "",
                "masked_value": masked,
            })

        return {"keys": entries, "total": len(entries)}
    except Exception as exc:
        logger.error("list_vault_masked error: %s", exc)
        return _safe_error(500, "Failed to list vault keys")


def _mask_value(value: str) -> str:
    """Mask a credential value: first 4 + **** + last 4."""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


@router.delete("/vault/keys/{key}")
async def delete_vault_key(key: str, request: Request):
    """Delete a credential from the vault."""
    try:
        if _connector_vault is None:
            return _safe_error(400, "Vault not initialised")

        deleted = _connector_vault.delete(key)
        if not deleted:
            return _safe_error(404, f"Key '{key}' not found in vault")

        _audit("SETUP_VAULT_DELETE", f"Deleted vault key: {key}", request=request)
        return {"status": "deleted", "key": key}
    except Exception as exc:
        logger.error("delete_vault_key error: %s", exc)
        return _safe_error(500, "Failed to delete vault key")


@router.post("/vault/reset")
async def reset_connector_vault(request: Request, body: VaultResetRequest):
    """Archive encrypted vault artifacts and restart into a clean vault state."""
    global _connector_vault, _connector_vault_error
    try:
        if not body.confirm or body.confirmation_text != "RESET CONNECTOR VAULT":
            return _safe_error(400, 'Type RESET CONNECTOR VAULT to confirm connector-vault reset')

        from src.connectors.vault import CredentialVault

        reset_result = CredentialVault.reset_storage(config_path=_connector_vault_config_path)
        archived_files = reset_result.get("archived_files", [])
        archive_dir = reset_result.get("archive_dir", "")

        _connector_vault = None
        _connector_vault_error = None

        details = (
            f"Connector vault reset staged. Archived {len(archived_files)} file(s)"
            f"{f' to {archive_dir}' if archive_dir else ''}."
        )
        _audit("SETUP_VAULT_RESET", details, request=request)
        logger.warning("%s Restarting container to clear in-memory vault state.", details)

        import threading

        threading.Timer(0.5, lambda: os._exit(0)).start()

        return {
            "status": "resetting",
            "message": "Connector vault artifacts archived. Container will restart momentarily.",
            "archived_files": archived_files,
            "archive_dir": archive_dir,
            "restart_required": True,
            "vault_status": CredentialVault.inspect_health(
                config_path=_connector_vault_config_path,
                last_error=None,
            ).to_dict(),
        }
    except Exception as exc:
        logger.error("reset_connector_vault error: %s", exc)
        return _safe_error(500, "Failed to reset connector vault")


# ------------------------------------------------------------------
# Receipt Management
# ------------------------------------------------------------------

@router.post("/receipts/clear")
async def clear_receipts(request: Request, body: ConfirmRequest):
    """Clear all execution receipts."""
    try:
        if not body.confirm:
            return _safe_error(400, "Confirmation required: {\"confirm\": true}")

        if _receipt_service is None:
            return _safe_error(400, "Receipt service not initialised")

        if hasattr(_receipt_service, 'clear'):
            _receipt_service.clear()
        elif hasattr(_receipt_service, '_receipts'):
            _receipt_service._receipts.clear()
            if hasattr(_receipt_service, '_save'):
                _receipt_service._save()

        _audit("SETUP_RECEIPTS_CLEAR", "All receipts cleared via War Room", request=request)
        return {"status": "cleared", "message": "All receipts have been cleared"}
    except Exception as exc:
        logger.error("clear_receipts error: %s", exc)
        return _safe_error(500, "Failed to clear receipts")


# ------------------------------------------------------------------
# Configuration Reload
# ------------------------------------------------------------------

@router.post("/config/reload")
async def reload_config(request: Request):
    """Re-read YAML configs and reload subsystems where possible."""
    try:
        results = {}

        # Reload feature flags
        try:
            import feature_flags as ff
            ff.reload_flags()
            results["feature_flags"] = "reloaded"
        except Exception as e:
            results["feature_flags"] = f"failed: {e}"

        # Reload scheduler config
        try:
            from gateway import scheduler_service
            if scheduler_service:
                count = scheduler_service.register_from_config()
                results["scheduler"] = f"reloaded ({count} jobs)"
            else:
                results["scheduler"] = "not running"
        except Exception as e:
            results["scheduler"] = f"failed: {e}"

        # Reload connector registry
        try:
            from connectors.registry import ConnectorRegistry
            registry = ConnectorRegistry(config_path="config/connectors.yaml")
            results["connectors"] = "reloaded"
        except Exception as e:
            results["connectors"] = f"failed: {e}"

        _audit("SETUP_CONFIG_RELOAD", f"Config reloaded: {results}", request=request)
        return {"status": "reloaded", "results": results}
    except Exception as exc:
        logger.error("reload_config error: %s", exc)
        return _safe_error(500, "Failed to reload config")


# ------------------------------------------------------------------
# Export / Backup
# ------------------------------------------------------------------

@router.get("/export")
async def export_backup(request: Request):
    """Generate and return a ZIP backup of config, soul, memory, flags."""
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Config files
            config_dir = Path("config")
            if config_dir.exists():
                for f in config_dir.glob("*.yaml"):
                    zf.write(f, f"config/{f.name}")
                for f in config_dir.glob("*.yml"):
                    zf.write(f, f"config/{f.name}")

            # Soul YAML
            soul_dir = _data_dir / "soul" if _data_dir else Path("/home/lancelot/data/soul")
            if soul_dir.exists():
                for f in soul_dir.glob("*.yaml"):
                    zf.write(f, f"soul/{f.name}")
                for f in soul_dir.glob("*.yml"):
                    zf.write(f, f"soul/{f.name}")

            # Memory core blocks
            core_blocks = _data_dir / "core_blocks.json" if _data_dir else Path("/home/lancelot/data/core_blocks.json")
            if core_blocks.exists():
                zf.write(core_blocks, "memory/core_blocks.json")

            # Flag state
            flag_state = _data_dir / ".flag_state.json" if _data_dir else Path("/home/lancelot/data/.flag_state.json")
            if flag_state.exists():
                zf.write(flag_state, "flags/.flag_state.json")

            # Scheduler data
            sched_dir = _data_dir / "scheduler" if _data_dir else Path("/home/lancelot/data/scheduler")
            if sched_dir.exists():
                for f in sched_dir.glob("*.json"):
                    zf.write(f, f"scheduler/{f.name}")
                for f in sched_dir.glob("*.yaml"):
                    zf.write(f, f"scheduler/{f.name}")

        buf.seek(0)
        _audit("SETUP_EXPORT", "Backup ZIP exported via War Room", request=request)

        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=lancelot-backup.zip"},
        )
    except Exception as exc:
        logger.error("export_backup error: %s", exc)
        return _safe_error(500, "Failed to generate backup")


# ------------------------------------------------------------------
# Danger Zone
# ------------------------------------------------------------------

@router.post("/factory-reset")
async def factory_reset(request: Request, body: FactoryResetRequest):
    """Nuclear option: delete data dir contents, reset flags, reset onboarding."""
    try:
        if not body.confirm or body.confirmation_text != "RESET":
            return _safe_error(400, "Type RESET to confirm factory reset")

        _audit("SETUP_FACTORY_RESET", "Factory reset initiated via War Room", request=request)

        # Stop all subsystems
        try:
            from subsystem_manager import subsystem_manager
            subsystem_manager.stop_all()
        except Exception as exc:
            logger.warning("Factory reset: failed to stop subsystems cleanly: %s", exc)

        # Delete data dir contents (preserve vault key env var — it's in Docker env)
        if _data_dir and _data_dir.exists():
            for item in _data_dir.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except Exception as e:
                    logger.warning("Factory reset: failed to delete %s: %s", item, e)

        # Reset flag state
        flag_state = _data_dir / ".flag_state.json" if _data_dir else Path("/home/lancelot/data/.flag_state.json")
        if flag_state.exists():
            flag_state.unlink(missing_ok=True)

        logger.warning("FACTORY RESET complete — data directory cleared")
        return {"status": "reset_complete", "message": "Factory reset complete. Restart recommended."}
    except Exception as exc:
        logger.error("factory_reset error: %s", exc)
        return _safe_error(500, "Failed to perform factory reset")


@router.post("/memory/purge")
async def purge_memory(request: Request, body: ConfirmRequest):
    """Clear all memory blocks and SQLite memory stores."""
    try:
        if not body.confirm:
            return _safe_error(400, "Confirmation required: {\"confirm\": true}")

        purged = []

        # Clear core_blocks.json
        core_blocks = _data_dir / "core_blocks.json" if _data_dir else Path("/home/lancelot/data/core_blocks.json")
        if core_blocks.exists():
            core_blocks.unlink()
            purged.append("core_blocks.json")

        # Clear SQLite memory stores
        if _data_dir:
            for db_file in _data_dir.glob("memory*.db"):
                db_file.unlink()
                purged.append(db_file.name)
            for db_file in _data_dir.glob("memory*.sqlite"):
                db_file.unlink()
                purged.append(db_file.name)

        _audit("SETUP_MEMORY_PURGE", f"Memory purged: {purged}", request=request)
        return {"status": "purged", "purged_files": purged}
    except Exception as exc:
        logger.error("purge_memory error: %s", exc)
        return _safe_error(500, "Failed to purge memory")


@router.post("/flags/reset")
async def reset_flags(request: Request, body: ConfirmRequest):
    """Reset all feature flags to code defaults by deleting .flag_state.json."""
    try:
        if not body.confirm:
            return _safe_error(400, "Confirmation required: {\"confirm\": true}")

        flag_state = _data_dir / ".flag_state.json" if _data_dir else Path("/home/lancelot/data/.flag_state.json")
        existed = flag_state.exists()
        if existed:
            flag_state.unlink()

        # Reload flags from env/defaults
        try:
            import feature_flags as ff
            ff.clear_persisted_flag_state()
            ff.reload_flags()
        except Exception as exc:
            logger.warning("Reset flags: failed to reload feature flags: %s", exc)

        _audit("SETUP_FLAGS_RESET", "Feature flags reset to defaults", request=request)
        return {
            "status": "reset",
            "message": "Feature flags reset to code defaults" + (" (state file deleted)" if existed else " (no state file found)"),
        }
    except Exception as exc:
        logger.error("reset_flags error: %s", exc)
        return _safe_error(500, "Failed to reset flags")
