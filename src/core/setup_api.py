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

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from update_checker import read_current_version

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup", tags=["setup"])

# Set by init_setup_api() at startup
_data_dir: Optional[Path] = None
_startup_time: Optional[float] = None
_audit_logger = None
_connector_vault = None
_receipt_service = None


def init_setup_api(
    data_dir: str,
    startup_time: float,
    audit_logger=None,
    connector_vault=None,
    receipt_service=None,
) -> None:
    """Initialise the setup API with references to subsystems."""
    global _data_dir, _startup_time, _audit_logger, _connector_vault, _receipt_service
    _data_dir = Path(data_dir)
    _startup_time = startup_time
    _audit_logger = audit_logger
    _connector_vault = connector_vault
    _receipt_service = receipt_service
    logger.info("Setup API initialised (data_dir=%s)", data_dir)


def _safe_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message, "status": status_code})


def _audit(event_type: str, details: str) -> None:
    """Log to audit trail if available."""
    if _audit_logger:
        try:
            _audit_logger.log_event(event_type, details, user="WarRoom")
        except Exception:
            pass


# ------------------------------------------------------------------
# System Info
# ------------------------------------------------------------------

@router.get("/system-info")
async def system_info():
    """Version, uptime, Python version, platform, disk usage."""
    try:
        uptime = round(time.time() - _startup_time, 1) if _startup_time else 0
        data_dir_info = {"path": str(_data_dir), "total_mb": 0, "used_mb": 0}
        if _data_dir and _data_dir.exists():
            try:
                usage = shutil.disk_usage(str(_data_dir))
                data_dir_info["total_mb"] = round(usage.total / (1024 * 1024), 1)
                data_dir_info["used_mb"] = round(usage.used / (1024 * 1024), 1)
            except Exception:
                pass

        hostname = ""
        try:
            hostname = os.environ.get("HOSTNAME", platform.node())
        except Exception:
            pass

        return {
            "version": read_current_version(),
            "uptime_seconds": uptime,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "hostname": hostname,
            "data_dir": data_dir_info,
        }
    except Exception as exc:
        logger.error("system_info error: %s", exc)
        return _safe_error(500, "Failed to retrieve system info")


# ------------------------------------------------------------------
# Container Controls
# ------------------------------------------------------------------

@router.post("/restart")
async def restart_container(request: Request):
    """Graceful restart — os._exit(0) so Docker restarts the container."""
    try:
        data = await request.json()
        if not data.get("confirm"):
            return _safe_error(400, "Confirmation required: {\"confirm\": true}")

        _audit("SETUP_RESTART", "Container restart initiated via War Room")
        logger.warning("RESTART initiated via Setup API — exiting with code 0")

        # Flush state before exit
        try:
            from subsystem_manager import subsystem_manager
            subsystem_manager.stop_all()
        except Exception:
            pass

        # Schedule exit after response is sent
        import threading
        threading.Timer(0.5, lambda: os._exit(0)).start()

        return {"status": "restarting", "message": "Container will restart momentarily"}
    except Exception as exc:
        logger.error("restart error: %s", exc)
        return _safe_error(500, "Failed to initiate restart")


@router.post("/shutdown")
async def shutdown_container(request: Request):
    """Graceful shutdown — os._exit(1) so Docker does NOT restart."""
    try:
        data = await request.json()
        if not data.get("confirm"):
            return _safe_error(400, "Confirmation required: {\"confirm\": true}")

        _audit("SETUP_SHUTDOWN", "Container shutdown initiated via War Room")
        logger.warning("SHUTDOWN initiated via Setup API — exiting with code 1")

        try:
            from subsystem_manager import subsystem_manager
            subsystem_manager.stop_all()
        except Exception:
            pass

        import threading
        threading.Timer(0.5, lambda: os._exit(1)).start()

        return {"status": "shutting_down", "message": "Container will shut down momentarily (will not auto-restart)"}
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


@router.delete("/vault/keys/{key}")
async def delete_vault_key(key: str):
    """Delete a credential from the vault."""
    try:
        if _connector_vault is None:
            return _safe_error(400, "Vault not initialised")

        deleted = _connector_vault.delete(key)
        if not deleted:
            return _safe_error(404, f"Key '{key}' not found in vault")

        _audit("SETUP_VAULT_DELETE", f"Deleted vault key: {key}")
        return {"status": "deleted", "key": key}
    except Exception as exc:
        logger.error("delete_vault_key error: %s", exc)
        return _safe_error(500, "Failed to delete vault key")


# ------------------------------------------------------------------
# Receipt Management
# ------------------------------------------------------------------

@router.post("/receipts/clear")
async def clear_receipts(request: Request):
    """Clear all execution receipts."""
    try:
        data = await request.json()
        if not data.get("confirm"):
            return _safe_error(400, "Confirmation required: {\"confirm\": true}")

        if _receipt_service is None:
            return _safe_error(400, "Receipt service not initialised")

        if hasattr(_receipt_service, 'clear'):
            _receipt_service.clear()
        elif hasattr(_receipt_service, '_receipts'):
            _receipt_service._receipts.clear()
            if hasattr(_receipt_service, '_save'):
                _receipt_service._save()

        _audit("SETUP_RECEIPTS_CLEAR", "All receipts cleared via War Room")
        return {"status": "cleared", "message": "All receipts have been cleared"}
    except Exception as exc:
        logger.error("clear_receipts error: %s", exc)
        return _safe_error(500, "Failed to clear receipts")


# ------------------------------------------------------------------
# Configuration Reload
# ------------------------------------------------------------------

@router.post("/config/reload")
async def reload_config():
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

        _audit("SETUP_CONFIG_RELOAD", f"Config reloaded: {results}")
        return {"status": "reloaded", "results": results}
    except Exception as exc:
        logger.error("reload_config error: %s", exc)
        return _safe_error(500, "Failed to reload config")


# ------------------------------------------------------------------
# Export / Backup
# ------------------------------------------------------------------

@router.get("/export")
async def export_backup():
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
        _audit("SETUP_EXPORT", "Backup ZIP exported via War Room")

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
async def factory_reset(request: Request):
    """Nuclear option: delete data dir contents, reset flags, reset onboarding."""
    try:
        data = await request.json()
        if not data.get("confirm") or data.get("confirmation_text") != "RESET":
            return _safe_error(400, "Type RESET to confirm factory reset")

        _audit("SETUP_FACTORY_RESET", "Factory reset initiated via War Room")

        # Stop all subsystems
        try:
            from subsystem_manager import subsystem_manager
            subsystem_manager.stop_all()
        except Exception:
            pass

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
async def purge_memory(request: Request):
    """Clear all memory blocks and SQLite memory stores."""
    try:
        data = await request.json()
        if not data.get("confirm"):
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

        _audit("SETUP_MEMORY_PURGE", f"Memory purged: {purged}")
        return {"status": "purged", "purged_files": purged}
    except Exception as exc:
        logger.error("purge_memory error: %s", exc)
        return _safe_error(500, "Failed to purge memory")


@router.post("/flags/reset")
async def reset_flags(request: Request):
    """Reset all feature flags to code defaults by deleting .flag_state.json."""
    try:
        data = await request.json()
        if not data.get("confirm"):
            return _safe_error(400, "Confirmation required: {\"confirm\": true}")

        flag_state = _data_dir / ".flag_state.json" if _data_dir else Path("/home/lancelot/data/.flag_state.json")
        existed = flag_state.exists()
        if existed:
            flag_state.unlink()

        # Reload flags from env/defaults
        try:
            import feature_flags as ff
            ff._persisted_state.clear()
            ff.reload_flags()
        except Exception:
            pass

        _audit("SETUP_FLAGS_RESET", "Feature flags reset to defaults")
        return {
            "status": "reset",
            "message": "Feature flags reset to code defaults" + (" (state file deleted)" if existed else " (no state file found)"),
        }
    except Exception as exc:
        logger.error("reset_flags error: %s", exc)
        return _safe_error(500, "Failed to reset flags")
