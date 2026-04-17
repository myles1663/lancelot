# Authentication Architecture

This document defines the target authentication model for Lancelot.

The design goal is to support three deployment shapes with one coherent identity model:

- personal use
- small internal teams
- enterprise environments with existing identity providers and group policy

## Design Principles

- Identity must be real. Lancelot needs a stable operator identity for governance receipts, approvals, and audit reporting.
- Authentication should be strong by default. Local convenience paths are allowed, but they cannot be the only model.
- Enterprise MFA should normally be enforced by the customer's identity provider, not reimplemented as the primary auth mechanism inside Lancelot.
- Authorization should stay coarse-grained. Lancelot does not need a complex SaaS RBAC system, but it does need capability-level access control.
- Break-glass access must exist, be explicit, and be auditable.

## Supported Modes

### 1. Local

For personal use and small teams without an IdP.

Characteristics:

- local username/password login
- bcrypt password storage
- owner-held password reset code for login-screen recovery
- session-based War Room authentication
- HttpOnly cookie-backed browser sessions
- optional second factor in a future phase
- governance receipts still record the authenticated operator

This mode should remain easy to operate, but it is no longer treated as a plaintext or weak-hash convenience layer.

### 2. Enterprise SSO

For organizations using a standard identity provider.

Target providers:

- Microsoft Entra ID
- Okta
- Auth0
- Ping
- Keycloak
- other OIDC-compliant providers

Characteristics:

- OIDC login for browser users
- installer and in-app onboarding can provision issuer URL, client ID, client secret, and optional allowed groups
- MFA enforced at the IdP
- stable operator identity sourced from IdP subject/object ID
- group claims mapped into coarse Lancelot capabilities
- governance receipts include auth source, operator identity, and group/capability snapshot

### 3. Break-Glass Local Admin

For bootstrap, offline recovery, and emergency access.

Characteristics:

- explicit local-only admin path
- strongly labeled in receipts as break-glass usage
- disabled or tightly scoped in enterprise deployments
- intended for recovery, not normal daily operation

## Authorization Model

Lancelot should use capability mapping instead of deep product RBAC.

Recommended capability set:

- `warroom.login`
- `governance.approve`
- `setup.admin`
- `credentials.manage`
- `host_bridge.use`
- `uab.use`
- `observability.read`
- `soul.admin`

In enterprise mode, IdP groups map to these capabilities through configuration.

In local mode, capabilities are granted by the configured local account policy.

## Governance Identity Requirements

Every governed action should capture:

- stable operator ID
- display name
- username/email
- session ID
- authentication method
- IP address when available
- capability snapshot used for the decision
- approver identity for approval workflows

This is the minimum needed to support reliable audit and approval reporting.

## MFA Strategy

### Enterprise mode

MFA should be delegated to the IdP. Lancelot should consume the resulting authenticated session and, where available, record auth-context claims that indicate MFA-backed sign-in.

### Local mode

Built-in MFA is still valuable, but it is a secondary priority after OIDC support and session hardening.

Recommended future order:

1. TOTP for local accounts
2. WebAuthn/passkeys for stronger local auth

## Session Model

Lancelot should converge on server-managed browser sessions for the War Room.

Target properties:

- server-issued session identifiers
- secure cookie transport for browser auth
- HttpOnly cookies instead of JavaScript-readable bearer storage
- explicit session timeout and renewal semantics
- session records linked to operator identity
- session state usable by governance reporting

API keys remain appropriate for programmatic access, but they should not be the primary browser-auth mechanism.

## Current State

As of the current hardening pass:

- War Room local passwords are stored using bcrypt for all new writes
- plaintext env passwords migrate to bcrypt on first vault migration
- local password reset codes also migrate into hashed storage
- legacy SHA-256 and plaintext password formats remain readable only for backward compatibility
- successful legacy login upgrades the stored secret to bcrypt
- login attempts are rate-limited per client IP
- installer and in-app onboarding now require an explicit auth model choice: `local` or `oidc`
- local installs now create username/password during setup and expose login-screen recovery via a reset code
- enterprise installs now support OIDC login initiation, callback handling, and Lancelot session creation
- War Room browser sessions now use HttpOnly cookies instead of bearer tokens in localStorage, and the SPA no longer receives a boot-injected API token

This is a transitional state, not the final enterprise authentication model.

## Recommended Implementation Order

1. Harden local authentication
   - bcrypt password storage
   - login throttling
   - remove weak token transport patterns

2. Move browser auth to secure session cookies
   - reduce token handling in frontend storage
   - align browser auth with enterprise session expectations

3. Add auth-provider abstraction
   - `local`
   - `oidc`
   - `break_glass_local_admin`
   - `api_key` for programmatic clients

4. Expand OIDC policy integration
   - stronger claim validation
   - richer group-to-capability mapping
   - enterprise logout/session policy alignment

5. Add optional local MFA
   - TOTP first
   - WebAuthn later

## Non-Goals

- Deep, multi-tenant SaaS RBAC
- Replacing the customer's IdP policy engine
- Building a custom MFA system before SSO exists

## Runtime Policy Consistency

Soul activation is treated as a live governance update, not just a file write. When an approved Soul version is activated, Lancelot now refreshes the in-memory runtime policy immediately so the active orchestrator, risk classifier, HIVE parent-Soul boundary, MCP permission evaluator, and Time-Travel policy surfaces follow the same active Soul without requiring a restart.
