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

- **AGPL users:** You can freely use, modify, and distribute Lancelot under AGPL-3.0 terms.
  The patent does not restrict AGPL-compliant usage.
- **Commercial licensees:** Organizations requiring commercial licensing (to avoid AGPL
  open-source requirements) should contact us for terms.
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

Lancelot is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

This means:
- You can use Lancelot freely for any purpose
- If you modify Lancelot and provide it as a network service, you must release your
  modifications under AGPL-3.0
- Organizations that cannot comply with AGPL terms can obtain a commercial license

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
