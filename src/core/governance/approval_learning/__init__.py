"""
Approval Pattern Learning (APL)

Observes owner approve/deny decisions, detects repeating patterns,
and proposes automation rules. The owner confirms rules — the system
never self-activates. Rules have circuit breakers, expiry, and instant
revocation. Soul defines what can and cannot be automated.

Not a replacement for the Trust Ledger:
- Trust Ledger: "Did the action succeed?" -> tier graduation
- APL: "Does the owner always say yes?" -> approval automation
"""
__version__ = "0.1.0"
