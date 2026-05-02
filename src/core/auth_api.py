"""
War Room Authentication API.

Supports two browser auth modes:
- local username/password with operator-scoped sessions
- enterprise OIDC login with the same Lancelot session model

Each session carries an OperatorIdentity that is injected into
governance receipts.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
import logging
import string
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Optional
from urllib.parse import urlencode

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.responses import RedirectResponse

from src.core.outbound_http import assert_url_allowed
from src.core.operator_identity import OperatorIdentity, resolve_operator_id

logger = logging.getLogger("lancelot.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = ""
    password: str = ""


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = ""
    reset_code: str = ""
    new_password: str = ""


class OidcExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange_code: str = ""


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = ""
    new_password: str = ""

# Session store: {token: {"expires_at": float, "username": str,
#                          "operator_identity": OperatorIdentity}}
_sessions: dict = {}
_login_failures: dict[str, list[float]] = {}

_SESSION_TIMEOUT = int(os.getenv("WARROOM_SESSION_TIMEOUT_MINUTES", "30")) * 60
_LOGIN_RATE_LIMIT_MAX_FAILURES = int(os.getenv("WARROOM_LOGIN_MAX_FAILURES", "10"))
_LOGIN_RATE_LIMIT_WINDOW_S = int(os.getenv("WARROOM_LOGIN_WINDOW_SECONDS", "300"))
_BCRYPT_ROUNDS = int(os.getenv("WARROOM_PASSWORD_BCRYPT_ROUNDS", "12"))
_SESSION_COOKIE_NAME = os.getenv("WARROOM_SESSION_COOKIE_NAME", "lancelot_session")
_SESSION_COOKIE_SAMESITE = os.getenv("WARROOM_SESSION_COOKIE_SAMESITE", "strict").strip().lower() or "strict"
_SESSION_COOKIE_DOMAIN = os.getenv("WARROOM_SESSION_COOKIE_DOMAIN", "").strip() or None
_OIDC_STATE_TTL_S = int(os.getenv("OIDC_STATE_TTL_SECONDS", "600"))
_OIDC_EXCHANGE_TTL_S = int(os.getenv("OIDC_EXCHANGE_TTL_SECONDS", "120"))
_oidc_pending: dict[str, dict] = {}
_oidc_exchange_codes: dict[str, dict] = {}
_AUTH_STATE_LOCK = threading.Lock()

_BASE_CAPABILITIES = {"warroom.login"}
_ADMIN_CAPABILITIES = {
    "platform.admin",
    "onboarding.admin",
    "provider.admin",
    "flags.admin",
    "setup.admin",
    "soul.admin",
    "governance.admin",
    "trust.admin",
    "apl.admin",
    "skills.admin",
    "connectors.admin",
    "observability.admin",
    "incidents.admin",
    "memory.admin",
    "federation.admin",
    "hive.admin",
    "compliance.admin",
    "scheduler.admin",
    "timetravel.admin",
    "a2a.admin",
    "mcp.admin",
}


async def _parse_request_model(request: Request, model_cls: type[BaseModel]) -> BaseModel:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON") from exc
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _get_auth_state_file() -> Path:
    return Path(os.getenv("LANCELOT_AUTH_STATE_FILE", "/home/lancelot/data/auth_state.json"))


def _get_auth_state_key_file() -> Path:
    configured = os.getenv("LANCELOT_AUTH_STATE_KEY_FILE", "").strip()
    if configured:
        return Path(configured)
    return _get_auth_state_file().with_suffix(".key")


def _restrict_auth_state_key_file_permissions(path: Path) -> None:
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            logger.warning("Failed to restrict auth state key permissions on %s: %s", path, exc)


def _load_or_create_auth_state_key() -> bytes:
    configured = os.getenv("LANCELOT_AUTH_STATE_ENCRYPTION_KEY", "").strip()
    if configured:
        return configured.encode("utf-8")

    key_file = _get_auth_state_key_file()
    if key_file.exists():
        return key_file.read_bytes()

    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    _restrict_auth_state_key_file_permissions(key_file)
    return key


def _get_auth_state_cipher() -> Fernet:
    return Fernet(_load_or_create_auth_state_key())


def _encrypt_auth_state_payload(payload: dict) -> str:
    plaintext = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _get_auth_state_cipher().encrypt(plaintext).decode("utf-8")


def _deserialize_auth_state_payload(raw_text: str) -> dict:
    text = (raw_text or "").strip()
    if not text:
        return {}

    outer = json.loads(text)
    if isinstance(outer, dict) and outer.get("encrypted") is True:
        ciphertext = outer.get("ciphertext", "")
        try:
            decrypted = _get_auth_state_cipher().decrypt(ciphertext.encode("utf-8"))
        except InvalidToken as exc:
            raise RuntimeError("Encrypted auth state could not be decrypted with the configured key") from exc
        return json.loads(decrypted.decode("utf-8"))
    return outer if isinstance(outer, dict) else {}


def _serialize_session(session: dict) -> dict:
    data = dict(session)
    identity = data.get("operator_identity")
    if isinstance(identity, OperatorIdentity):
        data["operator_identity"] = identity.to_dict()
    return data


def _deserialize_session(session: dict) -> dict:
    data = dict(session)
    identity = data.get("operator_identity")
    if isinstance(identity, dict):
        data["operator_identity"] = OperatorIdentity.from_dict(identity)
    return data


def _save_auth_state() -> None:
    payload = {
        "sessions": {token: _serialize_session(session) for token, session in _sessions.items()},
        "oidc_pending": _oidc_pending,
        "oidc_exchange_codes": _oidc_exchange_codes,
    }
    with _AUTH_STATE_LOCK:
        try:
            state_file = _get_auth_state_file()
            state_file.parent.mkdir(parents=True, exist_ok=True)
            envelope = {
                "version": 2,
                "encrypted": True,
                "algorithm": "fernet",
                "ciphertext": _encrypt_auth_state_payload(payload),
            }
            state_file.write_text(json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to persist auth state: %s", exc)


def _load_auth_state() -> None:
    global _sessions, _oidc_pending, _oidc_exchange_codes
    with _AUTH_STATE_LOCK:
        try:
            state_file = _get_auth_state_file()
            if not state_file.exists():
                _sessions = {}
                _oidc_pending = {}
                _oidc_exchange_codes = {}
                return
            payload = _deserialize_auth_state_payload(state_file.read_text(encoding="utf-8"))
            _sessions = {
                token: _deserialize_session(session)
                for token, session in dict(payload.get("sessions", {})).items()
            }
            _oidc_pending = dict(payload.get("oidc_pending", {}))
            _oidc_exchange_codes = dict(payload.get("oidc_exchange_codes", {}))
        except Exception as exc:
            logger.warning("Failed to load auth state: %s", exc)
            _sessions = {}
            _oidc_pending = {}
            _oidc_exchange_codes = {}


def _get_warroom_username() -> str:
    """Lazy-load War Room username from secret_cache (or env fallback)."""
    try:
        import secret_cache
        return secret_cache.get("WARROOM_USERNAME", "")
    except Exception:
        return os.getenv("WARROOM_USERNAME", "")


def _get_warroom_password() -> str:
    """Lazy-load War Room password hash from secret_cache (or env fallback)."""
    try:
        import secret_cache
        return secret_cache.get("WARROOM_PASSWORD", "")
    except Exception:
        return os.getenv("WARROOM_PASSWORD", "")


def _get_warroom_password_reset_code() -> str:
    """Lazy-load local password reset code hash from secret_cache (or env fallback)."""
    try:
        import secret_cache
        return secret_cache.get("WARROOM_PASSWORD_RESET_CODE", "")
    except Exception:
        return os.getenv("WARROOM_PASSWORD_RESET_CODE", "")


def _get_auth_provider() -> str:
    provider = os.getenv("LANCELOT_AUTH_PROVIDER", "").strip().lower()
    if provider in {"local", "oidc"}:
        return provider
    if _get_oidc_issuer_url() and _get_oidc_client_id():
        return "oidc"
    return "local"


def _local_auth_enabled() -> bool:
    return _get_auth_provider() == "local"


def _oidc_auth_enabled() -> bool:
    return _get_auth_provider() == "oidc"


def _password_reset_enabled() -> bool:
    return _local_auth_enabled() and bool(_get_warroom_password_reset_code())


def _get_oidc_issuer_url() -> str:
    return os.getenv("OIDC_ISSUER_URL", "").strip().rstrip("/")


def _get_oidc_client_id() -> str:
    return os.getenv("OIDC_CLIENT_ID", "").strip()


def _get_oidc_client_secret() -> str:
    return os.getenv("OIDC_CLIENT_SECRET", "").strip()


def _get_oidc_scopes() -> str:
    return os.getenv("OIDC_SCOPES", "openid profile email").strip()


def _get_oidc_display_name_claim() -> str:
    return os.getenv("OIDC_DISPLAY_NAME_CLAIM", "name").strip() or "name"


def _get_oidc_username_claim() -> str:
    return os.getenv("OIDC_USERNAME_CLAIM", "preferred_username").strip() or "preferred_username"


def _get_oidc_groups_claim() -> str:
    return os.getenv("OIDC_GROUPS_CLAIM", "groups").strip() or "groups"


def _get_oidc_allowed_groups() -> set[str]:
    raw = os.getenv("OIDC_ALLOWED_GROUPS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _oidc_allow_any_authenticated() -> bool:
    raw = os.getenv("OIDC_ALLOW_ANY_AUTHENTICATED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _get_oidc_admin_groups() -> set[str]:
    raw = os.getenv("OIDC_ADMIN_GROUPS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _get_oidc_redirect_uri(request: Request) -> str:
    configured = os.getenv("OIDC_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return str(request.url_for("oidc_callback"))


def _persist_secret(env_key: str, vault_key: str, stored_value: str) -> None:
    """Persist a canonical secret to vault/cache or env fallback."""
    try:
        import secret_cache
        if secret_cache.is_bootstrapped():
            from src.connectors.vault import CredentialVault

            vault = CredentialVault()
            vault.store(vault_key, stored_value, type="system_secret")
            secret_cache.set_cached(env_key, stored_value)
            return
    except Exception as exc:
        logger.warning("Vault update failed for %s, falling back to env: %s", env_key, exc)

    os.environ[env_key] = stored_value


def _verify_password(plain: str, stored: str) -> bool:
    """Verify a plaintext password against a stored value.

    Detects format:
    - bcrypt ($2b$...): bcrypt verify.
    - SHA-256 hex (64 chars): hash the input and compare.
    - Legacy plaintext: direct constant-time comparison.
    """
    if _is_bcrypt_hash(stored):
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), stored.encode("utf-8"))
        except ValueError:
            return False
    if _is_sha256_hex(stored):
        # Stored value looks like a SHA-256 hex digest
        input_hash = hashlib.sha256(plain.encode("utf-8")).hexdigest()
        return hmac.compare_digest(input_hash, stored)
    # Legacy plaintext comparison
    return hmac.compare_digest(plain, stored)


def _is_bcrypt_hash(stored: str) -> bool:
    return stored.startswith("$2a$") or stored.startswith("$2b$") or stored.startswith("$2y$")


def _is_sha256_hex(stored: str) -> bool:
    return len(stored) == 64 and all(ch in string.hexdigits for ch in stored)


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(
        plain.encode("utf-8"),
        bcrypt.gensalt(rounds=_BCRYPT_ROUNDS),
    ).decode("utf-8")


def _password_needs_upgrade(stored: str) -> bool:
    return not _is_bcrypt_hash(stored)


def _persist_warroom_password_secret(stored_value: str) -> None:
    """Persist the canonical stored password secret to vault/cache or env fallback."""
    _persist_secret("WARROOM_PASSWORD", "system.warroom_password_hash", stored_value)


def _persist_warroom_password_reset_code_secret(stored_value: str) -> None:
    """Persist the canonical stored reset code secret to vault/cache or env fallback."""
    _persist_secret(
        "WARROOM_PASSWORD_RESET_CODE",
        "system.warroom_password_reset_code_hash",
        stored_value,
    )


def _upgrade_password_hash_if_needed(plain_password: str, stored_value: str) -> bool:
    """Upgrade legacy password storage formats to bcrypt after successful auth."""
    if not _password_needs_upgrade(stored_value):
        return False
    upgraded = _hash_password(plain_password)
    _persist_warroom_password_secret(upgraded)
    logger.info("Upgraded War Room password secret to bcrypt format")
    return True

_audit_logger = None
_load_auth_state()


def init_auth_api(audit_logger=None):
    """Inject audit logger (called from gateway startup)."""
    global _audit_logger
    _audit_logger = audit_logger


def _cleanup_expired():
    now = time.time()
    expired = [t for t, s in _sessions.items() if s["expires_at"] < now]
    for t in expired:
        del _sessions[t]
    if expired:
        _save_auth_state()


def _session_cookie_secure(request: Request) -> bool:
    configured = os.getenv("WARROOM_SESSION_COOKIE_SECURE", "").strip().lower()
    if configured in {"1", "true", "yes"}:
        return True
    if configured in {"0", "false", "no"}:
        return False
    try:
        return str(request.url.scheme).lower() == "https"
    except Exception:
        return False


def _set_session_cookie(response: JSONResponse, token: str, request: Request) -> None:
    response.set_cookie(
        key=_SESSION_COOKIE_NAME,
        value=token,
        max_age=_SESSION_TIMEOUT,
        httponly=True,
        secure=_session_cookie_secure(request),
        samesite=_SESSION_COOKIE_SAMESITE,
        path="/",
        domain=_SESSION_COOKIE_DOMAIN,
    )


def _clear_session_cookie(response: JSONResponse, request: Request) -> None:
    response.delete_cookie(
        key=_SESSION_COOKIE_NAME,
        path="/",
        domain=_SESSION_COOKIE_DOMAIN,
        secure=_session_cookie_secure(request),
        samesite=_SESSION_COOKIE_SAMESITE,
    )


def _get_session_token_from_request(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    cookies = getattr(request, "cookies", {}) or {}
    return cookies.get(_SESSION_COOKIE_NAME, "")


def _json_session_response(payload: dict, token: str, request: Request) -> JSONResponse:
    body = {
        "expires_in": payload["expires_in"],
        "username": payload["username"],
        "operator_id": payload["operator_id"],
        "session_id": payload["session_id"],
    }
    response = JSONResponse(content=body)
    _set_session_cookie(response, token, request)
    return response


def _prune_login_failures(now: Optional[float] = None) -> None:
    now = now or time.time()
    expired_keys = []
    for key, attempts in _login_failures.items():
        recent = [t for t in attempts if t > now - _LOGIN_RATE_LIMIT_WINDOW_S]
        if recent:
            _login_failures[key] = recent
        else:
            expired_keys.append(key)
    for key in expired_keys:
        del _login_failures[key]


def _login_rate_limit_key(request: Request) -> str:
    """Rate-limit failed logins by client IP."""
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _is_login_rate_limited(request: Request) -> bool:
    _prune_login_failures()
    attempts = _login_failures.get(_login_rate_limit_key(request), [])
    return len(attempts) >= _LOGIN_RATE_LIMIT_MAX_FAILURES


def _record_login_failure(request: Request) -> None:
    now = time.time()
    key = _login_rate_limit_key(request)
    attempts = _login_failures.setdefault(key, [])
    attempts.append(now)
    _prune_login_failures(now)


def _clear_login_failures(request: Request) -> None:
    _login_failures.pop(_login_rate_limit_key(request), None)


def _cleanup_expired_oidc_state(now: Optional[float] = None) -> None:
    now = now or time.time()
    stale_states = [
        key for key, value in _oidc_pending.items()
        if value.get("created_at", 0) < now - _OIDC_STATE_TTL_S
    ]
    for key in stale_states:
        del _oidc_pending[key]

    stale_exchange_codes = [
        key for key, value in _oidc_exchange_codes.items()
        if value.get("expires_at", 0) < now
    ]
    for key in stale_exchange_codes:
        del _oidc_exchange_codes[key]
    if stale_states or stale_exchange_codes:
        _save_auth_state()


def _encode_pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _generate_oidc_flow(request: Request) -> dict:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(32)
    redirect_uri = _get_oidc_redirect_uri(request)
    flow = {
        "state": state,
        "code_verifier": verifier,
        "code_challenge": _encode_pkce_challenge(verifier),
        "nonce": nonce,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
    }
    _cleanup_expired_oidc_state(flow["created_at"])
    _oidc_pending[state] = flow
    _save_auth_state()
    return flow


def _create_session(
    *,
    username: str,
    auth_method: str,
    request: Request,
    operator_seed: Optional[str] = None,
    capabilities: Optional[set[str]] = None,
    groups: Optional[list[str]] = None,
) -> dict:
    token = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    client_ip = request.client.host if request.client else ""
    operator_identity = OperatorIdentity(
        operator_id=resolve_operator_id(operator_seed or username),
        display_name=username,
        session_id=session_id,
        session_started_at=now_iso,
        auth_method=auth_method,
        ip_address=client_ip,
    )
    _sessions[token] = {
        "expires_at": time.time() + _SESSION_TIMEOUT,
        "username": username,
        "operator_identity": operator_identity,
        "capabilities": sorted(capabilities or _BASE_CAPABILITIES),
        "groups": list(groups or []),
    }
    _cleanup_expired()
    _save_auth_state()
    return {
        "token": token,
        "expires_in": _SESSION_TIMEOUT,
        "username": username,
        "operator_id": operator_identity.operator_id,
        "session_id": session_id,
    }


def _capabilities_for_local_auth() -> set[str]:
    return set(_BASE_CAPABILITIES | _ADMIN_CAPABILITIES)


def _capabilities_for_api_key() -> set[str]:
    return set(_BASE_CAPABILITIES | _ADMIN_CAPABILITIES)


def _capabilities_for_oidc_groups(groups: list[str]) -> set[str]:
    capabilities = set(_BASE_CAPABILITIES)
    admin_groups = _get_oidc_admin_groups()
    if admin_groups and (admin_groups & set(groups)):
        capabilities |= _ADMIN_CAPABILITIES
    return capabilities


def _require_oidc_config() -> Optional[str]:
    missing = []
    if not _get_oidc_issuer_url():
        missing.append("OIDC_ISSUER_URL")
    if not _get_oidc_client_id():
        missing.append("OIDC_CLIENT_ID")
    if not _get_oidc_client_secret():
        missing.append("OIDC_CLIENT_SECRET")
    if missing:
        return ", ".join(missing)
    return None


def _get_oidc_metadata() -> dict:
    missing = _require_oidc_config()
    if missing:
        raise RuntimeError(f"Missing OIDC configuration: {missing}")

    issuer = _get_oidc_issuer_url()
    import httpx

    well_known = issuer + "/.well-known/openid-configuration"
    assert_url_allowed(well_known, component="OIDC metadata fetch")
    response = httpx.get(well_known, timeout=10.0)
    response.raise_for_status()
    metadata = response.json()
    return metadata


def _build_oidc_authorize_url(metadata: dict, request: Request) -> tuple[str, str]:
    flow = _generate_oidc_flow(request)
    params = {
        "client_id": _get_oidc_client_id(),
        "response_type": "code",
        "redirect_uri": flow["redirect_uri"],
        "scope": _get_oidc_scopes(),
        "state": flow["state"],
        "nonce": flow["nonce"],
        "code_challenge": flow["code_challenge"],
        "code_challenge_method": "S256",
    }
    authorize_url = metadata["authorization_endpoint"] + "?" + urlencode(params)
    return authorize_url, flow["state"]


def _extract_claims_profile(claims: dict) -> tuple[str, str, list[str]]:
    username_claim = _get_oidc_username_claim()
    display_claim = _get_oidc_display_name_claim()
    groups_claim = _get_oidc_groups_claim()

    username = (
        claims.get(username_claim)
        or claims.get("email")
        or claims.get("sub")
        or "enterprise-user"
    )
    display_name = (
        claims.get(display_claim)
        or claims.get("name")
        or claims.get("email")
        or username
    )
    raw_groups = claims.get(groups_claim, [])
    if isinstance(raw_groups, str):
        groups = [raw_groups]
    elif isinstance(raw_groups, list):
        groups = [str(item) for item in raw_groups]
    else:
        groups = []
    return str(username), str(display_name), groups


def _enforce_allowed_groups(groups: list[str]) -> None:
    allowed = _get_oidc_allowed_groups()
    if not allowed:
        if _oidc_allow_any_authenticated():
            logger.warning(
                "OIDC_ALLOW_ANY_AUTHENTICATED is enabled; granting War Room access to any authenticated OIDC user"
            )
            return
        raise PermissionError(
            "OIDC_ALLOWED_GROUPS must be configured unless OIDC_ALLOW_ANY_AUTHENTICATED=true is explicitly set"
        )
    if not (allowed & set(groups)):
        raise PermissionError("User is not a member of an allowed enterprise group")


async def _complete_oidc_auth(request: Request, code: str, state: str) -> dict:
    flow = _oidc_pending.pop(state, None)
    if not flow:
        raise ValueError("OIDC state is missing or expired")

    metadata = _get_oidc_metadata()
    import httpx

    token_endpoint = assert_url_allowed(
        metadata["token_endpoint"],
        component="OIDC token exchange",
    )
    token_response = httpx.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": flow["redirect_uri"],
            "client_id": _get_oidc_client_id(),
            "client_secret": _get_oidc_client_secret(),
            "code_verifier": flow["code_verifier"],
        },
        timeout=15.0,
    )
    token_response.raise_for_status()
    token_data = token_response.json()

    claims = {}
    userinfo_endpoint = metadata.get("userinfo_endpoint")
    access_token = token_data.get("access_token", "")
    if userinfo_endpoint and access_token:
        userinfo_endpoint = assert_url_allowed(
            userinfo_endpoint,
            component="OIDC userinfo fetch",
        )
        userinfo_response = httpx.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        userinfo_response.raise_for_status()
        claims = userinfo_response.json()

    if not claims:
        raise RuntimeError("OIDC userinfo endpoint did not return claims")

    username, display_name, groups = _extract_claims_profile(claims)
    _enforce_allowed_groups(groups)

    session = _create_session(
        username=display_name,
        auth_method="sso",
        request=request,
        operator_seed=f"{_get_oidc_issuer_url()}|{claims.get('sub', username)}",
        capabilities=_capabilities_for_oidc_groups(groups),
        groups=groups,
    )
    exchange_code = secrets.token_urlsafe(24)
    _oidc_exchange_codes[exchange_code] = {
        **session,
        "expires_at": time.time() + _OIDC_EXCHANGE_TTL_S,
    }
    _save_auth_state()
    return {
        "exchange_code": exchange_code,
        "username": session["username"],
    }


@router.post("/login")
async def login(request: Request):
    if not _local_auth_enabled():
        return JSONResponse(
            status_code=400,
            content={"error": "Local login is disabled for enterprise authentication mode"},
        )

    if _is_login_rate_limited(request):
        if _audit_logger:
            _audit_logger.log_event(
                "AUTH_LOGIN_RATE_LIMITED",
                "Login blocked by rate limiter",
                user=_login_rate_limit_key(request),
            )
        return JSONResponse(status_code=429, content={"error": "Too many login attempts. Try again later."})

    body = await _parse_request_model(request, LoginRequest)
    username = body.username
    password = body.password

    wr_user = _get_warroom_username()
    wr_pass = _get_warroom_password()

    if not wr_user or not wr_pass:
        return JSONResponse(status_code=503, content={
            "error": "War Room credentials not configured",
            "detail": "Set WARROOM_USERNAME and WARROOM_PASSWORD environment variables",
        })

    if not (hmac.compare_digest(username, wr_user)
            and _verify_password(password, wr_pass)):
        _record_login_failure(request)
        if _audit_logger:
            _audit_logger.log_event(
                "AUTH_LOGIN_FAILED",
                f"Failed login attempt for user: {username}",
                user=username,
            )
        return JSONResponse(status_code=401, content={"error": "Invalid credentials"})

    _upgrade_password_hash_if_needed(password, wr_pass)

    session = _create_session(
        username=username,
        auth_method="local",
        request=request,
        capabilities=_capabilities_for_local_auth(),
    )
    _clear_login_failures(request)

    if _audit_logger:
        _audit_logger.log_event(
            "AUTH_LOGIN_SUCCESS",
            f"User {username} logged in (operator_id={session['operator_id']}, session={session['session_id']})",
            user=username,
        )

    return _json_session_response(session, session["token"], request)


@router.get("/config")
async def auth_config():
    provider = _get_auth_provider()
    allowed_groups = _get_oidc_allowed_groups()
    open_access = _oidc_allow_any_authenticated()
    data = {
        "provider": provider,
        "local": {
            "enabled": provider == "local",
            "password_reset_enabled": _password_reset_enabled(),
        },
        "oidc": {
            "enabled": provider == "oidc",
            "configured": _require_oidc_config() is None and (bool(allowed_groups) or open_access),
            "display_name": os.getenv("OIDC_LOGIN_BUTTON_TEXT", "Continue with Enterprise SSO"),
            "login_path": "/auth/oidc/login",
            "allowed_groups_configured": bool(allowed_groups),
            "allow_any_authenticated": open_access,
        },
    }
    if provider == "local":
        data["local"]["username_hint"] = _get_warroom_username()
    return data


@router.post("/reset-password")
async def reset_password(request: Request):
    if not _local_auth_enabled():
        return JSONResponse(
            status_code=400,
            content={"error": "Password reset is only available in local authentication mode"},
        )

    body = await _parse_request_model(request, ResetPasswordRequest)
    username = body.username
    reset_code = body.reset_code
    new_password = body.new_password

    if not username or not reset_code or not new_password:
        return JSONResponse(status_code=400, content={"error": "Username, reset code, and new password are required"})

    if len(new_password) < 8:
        return JSONResponse(status_code=400, content={"error": "New password must be at least 8 characters"})

    wr_user = _get_warroom_username()
    reset_secret = _get_warroom_password_reset_code()
    if not wr_user or not reset_secret:
        return JSONResponse(status_code=503, content={"error": "Password reset is not configured"})

    if not hmac.compare_digest(username, wr_user) or not _verify_password(reset_code, reset_secret):
        if _audit_logger:
            _audit_logger.log_event(
                "AUTH_PASSWORD_RESET_FAILED",
                f"Password reset failed for {username}",
                user=username,
            )
        return JSONResponse(status_code=403, content={"error": "Invalid username or reset code"})

    _persist_warroom_password_secret(_hash_password(new_password))
    if _audit_logger:
        _audit_logger.log_event(
            "AUTH_PASSWORD_RESET",
            f"Password reset completed for {username}",
            user=username,
        )
    logger.info("Password reset completed for user %s", username)
    return {"status": "ok", "message": "Password reset successfully"}


@router.get("/oidc/login")
async def oidc_login(request: Request):
    if not _oidc_auth_enabled():
        return JSONResponse(status_code=400, content={"error": "Enterprise OIDC login is not enabled"})
    try:
        metadata = _get_oidc_metadata()
        authorize_url, _state = _build_oidc_authorize_url(metadata, request)
        return RedirectResponse(authorize_url, status_code=302)
    except Exception as exc:
        logger.error("OIDC login initialization failed: %s", exc)
        return JSONResponse(status_code=503, content={"error": f"OIDC login initialization failed: {exc}"})


@router.get("/oidc/callback", name="oidc_callback")
async def oidc_callback(request: Request):
    if not _oidc_auth_enabled():
        return JSONResponse(status_code=400, content={"error": "Enterprise OIDC login is not enabled"})

    error = request.query_params.get("error")
    if error:
        description = request.query_params.get("error_description", error)
        redirect_path = "/war-room/login/callback#error=" + description
        return RedirectResponse(redirect_path, status_code=302)

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    if not code or not state:
        return JSONResponse(status_code=400, content={"error": "Missing OIDC code or state"})

    try:
        result = await _complete_oidc_auth(request, code, state)
        redirect_path = "/war-room/login/callback" f"#exchange_code={result['exchange_code']}"
        return RedirectResponse(redirect_path, status_code=302)
    except PermissionError as exc:
        logger.warning("OIDC login denied: %s", exc)
        return RedirectResponse("/war-room/login/callback#error=access_denied", status_code=302)
    except Exception as exc:
        logger.error("OIDC callback failed: %s", exc)
        return RedirectResponse("/war-room/login/callback#error=oidc_callback_failed", status_code=302)


@router.post("/oidc/exchange")
async def oidc_exchange(request: Request):
    if not _oidc_auth_enabled():
        return JSONResponse(status_code=400, content={"error": "Enterprise OIDC login is not enabled"})

    body = await _parse_request_model(request, OidcExchangeRequest)
    exchange_code = body.exchange_code
    if not exchange_code:
        return JSONResponse(status_code=400, content={"error": "exchange_code is required"})

    _cleanup_expired_oidc_state()
    session = _oidc_exchange_codes.pop(exchange_code, None)
    if not session:
        return JSONResponse(status_code=400, content={"error": "OIDC login exchange code is missing or expired"})
    _save_auth_state()

    return _json_session_response(session, session["token"], request)


@router.post("/validate")
async def validate_token(request: Request):
    token = _get_session_token_from_request(request)
    if not token:
        return JSONResponse(status_code=401, content={"valid": False})
    session = _sessions.get(token)
    if not session or session["expires_at"] < time.time():
        if session:
            del _sessions[token]
            _save_auth_state()
        return JSONResponse(status_code=401, content={"valid": False})
    # Refresh session on validate (extends timeout when user clicks "Stay Signed In")
    session["expires_at"] = time.time() + _SESSION_TIMEOUT
    _save_auth_state()
    remaining = session["expires_at"] - time.time()
    response = JSONResponse(content={
        "valid": True,
        "remaining_seconds": int(remaining),
        "username": session["username"],
    })
    _set_session_cookie(response, token, request)
    return response


@router.post("/logout")
async def logout(request: Request):
    token = _get_session_token_from_request(request)
    if token:
        removed = _sessions.pop(token, None)
        if removed:
            _save_auth_state()
        if removed and _audit_logger:
            _audit_logger.log_event(
                "AUTH_LOGOUT",
                f"User {removed['username']} logged out",
                user=removed["username"],
            )
    response = JSONResponse(content={"status": "ok"})
    _clear_session_cookie(response, request)
    return response


@router.post("/change-password")
async def change_password(request: Request):
    """Change the War Room password. Requires valid session + current password."""
    if not _local_auth_enabled():
        return JSONResponse(
            status_code=400,
            content={"error": "Password changes are only available in local authentication mode"},
        )

    token = _get_session_token_from_request(request)
    if not token:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    session = _sessions.get(token)
    if not session or session["expires_at"] < time.time():
        return JSONResponse(status_code=401, content={"error": "Session expired"})

    body = await _parse_request_model(request, ChangePasswordRequest)
    current_password = body.current_password
    new_password = body.new_password

    if not current_password or not new_password:
        return JSONResponse(status_code=400, content={"error": "Both current and new password are required"})

    if len(new_password) < 6:
        return JSONResponse(status_code=400, content={"error": "New password must be at least 6 characters"})

    # Verify current password
    wr_pass = _get_warroom_password()
    if not _verify_password(current_password, wr_pass):
        if _audit_logger:
            _audit_logger.log_event(
                "AUTH_PASSWORD_CHANGE_FAILED",
                f"Password change failed for {session['username']}: incorrect current password",
                user=session["username"],
            )
        return JSONResponse(status_code=403, content={"error": "Current password is incorrect"})

    # Hash new password and persist to vault + secret_cache
    new_hash = _hash_password(new_password)
    _persist_warroom_password_secret(new_hash)

    if _audit_logger:
        _audit_logger.log_event(
            "AUTH_PASSWORD_CHANGED",
            f"Password changed for {session['username']}",
            user=session["username"],
        )

    logger.info("Password changed for user %s", session["username"])
    return {"status": "ok", "message": "Password changed successfully"}


def verify_warroom_session_token(token: str) -> bool:
    """Check if a raw token string is a valid War Room session."""
    session = _sessions.get(token)
    if not session:
        return False
    if session["expires_at"] < time.time():
        del _sessions[token]
        _save_auth_state()
        return False
    return True


def get_warroom_session_cookie_name() -> str:
    return _SESSION_COOKIE_NAME


def verify_warroom_session(request: Request) -> bool:
    """Check if request has a valid War Room session token."""
    token = _get_session_token_from_request(request)
    if not token:
        return False
    session = _sessions.get(token)
    if not session:
        return False
    if session["expires_at"] < time.time():
        del _sessions[token]
        _save_auth_state()
        return False
    return True


def resolve_operator_identity(request: Request) -> Optional[OperatorIdentity]:
    """Resolve OperatorIdentity from a request's session token.

    Returns the OperatorIdentity if the request carries a valid War Room
    session. Returns None if the request is unauthenticated or uses only
    an API token (not a session).

    For API-token-only requests (no War Room session), callers can
    construct an OperatorIdentity from LANCELOT_OPERATOR_NAME env var
    with auth_method="api_key".
    """
    token = _get_session_token_from_request(request)
    if not token:
        return None
    session = _sessions.get(token)
    if not session:
        return None
    if session["expires_at"] < time.time():
        del _sessions[token]
        _save_auth_state()
        return None
    # Refresh session timeout on activity
    session["expires_at"] = time.time() + _SESSION_TIMEOUT
    _save_auth_state()
    return session.get("operator_identity")


def resolve_authenticated_identity(request: Request) -> OperatorIdentity:
    """Resolve the authenticated human/operator identity for the request.

    Session-backed browser requests return the session operator. API-token
    requests fall back to the configured API-key identity.
    """
    identity = resolve_operator_identity(request)
    if identity is not None:
        return identity
    return get_api_key_identity(request)


def resolve_request_capabilities(request: Request) -> set[str]:
    """Resolve coarse authorization capabilities for the authenticated request."""
    token = _get_session_token_from_request(request)
    if token:
        session = _sessions.get(token)
        if session:
            if session["expires_at"] < time.time():
                del _sessions[token]
                _save_auth_state()
                return set()
            session["expires_at"] = time.time() + _SESSION_TIMEOUT
            _save_auth_state()
            return set(session.get("capabilities", []))
    return _capabilities_for_api_key()


def request_has_capability(request: Request, capability: str) -> bool:
    """Return True when the authenticated request has the named capability."""
    return capability in resolve_request_capabilities(request)


def require_operator_capability(capability: str):
    """FastAPI dependency factory for coarse operator authorization checks."""

    def _dependency(request: Request) -> None:
        if not request_has_capability(request, capability):
            raise HTTPException(status_code=403, detail=f"Missing capability: {capability}")

    return _dependency


def get_api_key_identity(request: Request) -> OperatorIdentity:
    """Build an OperatorIdentity for API-key-authenticated requests.

    Uses LANCELOT_OPERATOR_NAME env var (defaults to War Room username).
    auth_method is set to "api_key".
    """
    operator_name = os.getenv("LANCELOT_OPERATOR_NAME", "")
    if not operator_name:
        operator_name = _get_warroom_username() or "operator"
    client_ip = request.client.host if request.client else ""
    return OperatorIdentity(
        operator_id=resolve_operator_id(operator_name),
        display_name=operator_name,
        session_id="",
        session_started_at="",
        auth_method="api_key",
        ip_address=client_ip,
    )
