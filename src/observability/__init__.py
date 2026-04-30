# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Observability Integration — OpenTelemetry, Webhooks, Metrics API.

Enterprise observability layer that translates Lancelot's governance data
into standard formats (OTel traces/metrics, webhook events, REST metrics)
without modifying the receipt system.
"""
