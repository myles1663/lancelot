"""
Lancelot vNext4: Risk-Tiered Governance Module

This module owns all risk-tiered governance logic:
- Risk classification (risk_classifier.py)
- Policy caching (policy_cache.py)
- Async verification (async_verifier.py)
- Intent templates (intent_templates.py)
- Batch receipts (batch_receipts.py)
- Configuration (config.py)
- War Room panel (war_room_panel.py)

Feature flags:
- FEATURE_RISK_TIERED_GOVERNANCE: Master switch
- FEATURE_POLICY_CACHE: Boot-time policy compilation
- FEATURE_ASYNC_VERIFICATION: Async verify for T1 actions
- FEATURE_INTENT_TEMPLATES: Cached plan templates
- FEATURE_BATCH_RECEIPTS: Batched receipt emission
"""

__version__ = "0.1.0"
