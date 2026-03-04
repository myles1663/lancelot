# Project Disclosure

## Patent Status

Lancelot is **Patent Pending** under US Provisional Patent Application #63/982,183,
filed February 2026, titled "Governed Autonomous System with Constitutional Governance,
Runtime-Switchable Postures, and Progressive Trust Graduation."

The patent covers the specific architectural methods and systems implemented in Lancelot,
including but not limited to: the Soul constitutional governance architecture, Crusader Mode
governance posture transformation, dependency-resolved Kill Switch management, risk-tiered
governance pipeline (T0-T3), progressive Trust Ledger graduation and revocation, governed
connector proxy pattern, and the integrated Governed Autonomous System (GAS) architecture.

### What This Means for Users

- **Non-production users:** You can freely use, modify, and study Lancelot under BSL 1.1 terms
  for evaluation, development, and testing. The patent does not restrict BSL-compliant usage.
- **Production users:** Production use requires a commercial license from the Licensor.
  Contact us for terms.
- **After the Change Date (March 4, 2030):** Lancelot automatically converts to AGPL-3.0-or-later,
  permitting full production use under AGPL terms.
- **Clean-room reimplementations:** The patent claims cover the specific architectural
  methods described in the filing. Independent implementations of the same methods may
  be subject to patent claims regardless of whether they use Lancelot's code.

## Development Methodology

Lancelot was architected and built entirely through AI-assisted development using
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) by Anthropic. No code
was written by hand.

The development process followed a specification-first methodology:
1. Detailed technical specifications were written for each subsystem
2. Step-by-step implementation blueprints were created with sequential prompts
3. Claude Code generated all source code, tests, and configuration
4. The test suite validates correctness across all subsystems

The original specifications and blueprints are preserved in `docs/internal/` as
architectural reference and methodology documentation.

This approach demonstrates that the core value of Lancelot lies in its architectural
design and governance philosophy — not in any particular code implementation.

## Inventor & Maintainer

Lancelot was created by **Myles Russell Hamilton**.

- GitHub: [@myles1663](https://github.com/myles1663)
- Project: [ProjectLancelot.com](https://projectlancelot.com)

## Licensing

Lancelot is source-available under the **Business Source License 1.1 (BSL 1.1)**.

This means:
- You can freely use, copy, modify, and redistribute Lancelot for non-production purposes
  (evaluation, development, testing, personal projects)
- Production use requires a commercial license from the Licensor
- On the Change Date (March 4, 2030), the license automatically converts to AGPL-3.0-or-later

See [LICENSE](LICENSE) for the full license text.

## Current Limitations

Lancelot is in active early-stage development. Current known limitations include:

- **Connector coverage:** Gmail and Telegram connectors are production-tested. Slack,
  Calendar, Teams, Discord, WhatsApp, and SMS connectors are implemented but have
  limited production hours.
- **Community:** This is a new project. Community support resources are being established.
- **Production hours:** While the test suite is comprehensive (1900+ tests), real-world
  production runtime hours are limited. Please report issues via GitHub Issues.
- **Single maintainer:** Lancelot is currently maintained by a single developer.
  Response times on issues may vary.

## Responsible Disclosure

For security vulnerabilities, please see [SECURITY.md](SECURITY.md) for our
responsible disclosure process. Do NOT file security issues as public GitHub Issues.
