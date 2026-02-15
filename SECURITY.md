# Security Policy

## Reporting a Vulnerability

**DO NOT** file security vulnerabilities as public GitHub Issues.

For responsible disclosure, email: **security@projectlancelot.com**

We will:
- Acknowledge receipt within 48 hours
- Provide an initial assessment within 7 days
- Work with you on a fix timeline
- Credit you in the security advisory (unless you prefer anonymity)

## Scope

The following are in scope for security reports:
- Governance bypass (circumventing Soul constraints)
- Prompt injection that evades detection
- Credential exposure (Vault, API keys, tokens)
- Receipt tampering or audit trail manipulation
- Unauthorized escalation of trust tiers
- Network allowlist bypass
- Kill switch circumvention
- Memory quarantine bypass
- Sandbox escape

## Security Architecture

For details on Lancelot's security posture, threat model, and enforcement layers,
see [Security Posture](docs/security.md).

## Patent Pending

Lancelot's governance architecture is protected under US Provisional Patent Application
#63/982,183. See [DISCLOSURE.md](DISCLOSURE.md) for details.
