# Roadmap

Future development plans for Universal App Bridge.

> This roadmap reflects current thinking and is subject to change based on user feedback and priorities.

## Completed

### Phase 1 — Core Framework (v0.5.0)
- ✅ Smart Function Discovery pipeline (scan → identify → register → connect → learn)
- ✅ Framework detection via DLL module scanning (10 framework signatures)
- ✅ Plugin architecture with ordered priority cascade (9 plugins)
- ✅ Unified API (detect, enumerate, query, act, state)
- ✅ Electron CDP plugin (full DevTools Protocol integration)
- ✅ Windows UIA universal fallback (1500+ LOC)
- ✅ Office COM+UIA hybrid (Word, Excel, PowerPoint, Outlook)
- ✅ Qt, GTK, Java, Flutter UIA bridge plugins
- ✅ Smart three-tier cache (tree 5s, query 3s, state 2s)
- ✅ Permission model with risk classification + audit log
- ✅ Connection health monitoring with auto-reconnect
- ✅ Retry with exponential backoff and jitter
- ✅ Action chains (multi-step workflow engine)
- ✅ JSON-only CLI for AI agents

### Phase 2 — Browser Automation (v0.6.0)
- ✅ Chrome Extension bridge (Manifest V3, WebSocket)
- ✅ Browser CDP fallback plugin
- ✅ Tab management (list, open, close, switch)
- ✅ Cookie CRUD operations
- ✅ localStorage / sessionStorage access
- ✅ JavaScript execution in page context
- ✅ Browser navigation (back, forward, reload)
- ✅ Extension auto-reconnect (25s keepalive)

### Phase 3 — Smart Connector & Registry (v0.7.0)
- ✅ `UABConnector` — Framework-independent, instantiable API (zero dependencies)
- ✅ `AppRegistry` — In-memory knowledge base with JSON persistence
- ✅ Dual-indexed Maps (O(1) lookup by PID and executable name)
- ✅ Smart lookup: registry first, live detection fallback
- ✅ Learning loop: preferred method updated after successful connection
- ✅ Batch DLL scanning (50 PIDs per PowerShell call)
- ✅ Batch window title scanning (single P/Invoke call)
- ✅ CLI commands: `scan`, `apps`, `find`, `profiles`
- ✅ Git-friendly JSON profile persistence (`registry.json`)
- ✅ Comprehensive documentation reflecting Smart Function Discovery

### Phase 4 — Desktop + Server Dual-Mode (v0.8.0)
- ✅ Environment auto-detection (desktop / server / container)
- ✅ UABConnector auto-tuning (persistence, rate limits, cache TTL per environment)
- ✅ HTTP REST server (`UABServer`) for remote agent access
- ✅ Full API exposed as JSON endpoints (scan, connect, query, act, etc.)
- ✅ Optional API key authentication (`X-API-Key` header)
- ✅ CORS support for browser-based agents
- ✅ Session 0→1 bridge integration (transparent for server mode)
- ✅ CLI `serve` command for starting the HTTP server
- ✅ CLI `env` command for environment inspection
- ✅ ONE codebase — desktop and server, no separate builds
- ✅ Documentation updated with server-side usage and HTTP API reference

### Phase 5 — Vision, Installer & Co-work Bridge (v1.0.0)
- ✅ Vision fallback plugin (screenshot + coordinate-based clicking as final cascade fallback)
- ✅ GUI installer (Electron-based one-click setup: service, Chrome extension, skill files, API key)
- ✅ CLI installer (`node dist/cli.js install`)
- ✅ Co-work bridge via Chrome extension relay (leverages existing trust, no new ports)
- ✅ Electron multi-process PID resolution (prefers visible window over broker/crashpad/GPU subprocesses)
- ✅ Confirmed Electron PID fix for: ChatGPT, VS Code, Slack, Discord, Teams, Notion, Obsidian
- ✅ Flow Library with recursive application bridge pattern — per-app interaction sequences with framework defaults

### Phase 6 — Anti-Screenshot SDK (v1.2.0)
- ✅ Spatial maps, composite engine, MCP server, atomic chains, smart invoke, focus tracking

### Phase 7 — The Concerto / P6 Raw Input Injection (v1.3.0)
- ✅ OS-level mouse drag injection via `SendInput()` — `/drag` endpoint with left/middle/right button support
- ✅ OS-level scroll wheel injection — `/scroll` endpoint at arbitrary coordinates
- ✅ `drag` action type in Vision plugin with waypoint paths and simple A→B drag
- ✅ Concerto Principle in flow library — per-operation method selection (speed + outcome + control + cost)
- ✅ Blender sculpting verified — first AI agent to sculpt 3D geometry (keyboard + drag + screenshot concerto)
- ✅ 6-tier cascade: P1 Extension → P2 CDP → P3 Framework → P4 UIA → P5 Keyboard → P6 OS Input Injection
- ✅ Vision-guided drag loop: screenshot → plan path → inject drag → screenshot → verify → repeat

---

## Near-Term

### Registry Intelligence

Enhance the learning loop:

- Confidence decay: lower confidence for apps not seen recently
- Framework re-detection: verify framework hasn't changed after updates
- PID staleness detection: mark stale PIDs after process restart
- Connection method history: track success/failure rates per method per app

### macOS Support

Extend plugin coverage:

- **NSAccessibility plugin** — Native macOS accessibility API
- **Electron/CDP** — Already cross-platform
- **AppleScript bridge** — macOS-specific automation

### Linux Support

- **AT-SPI2 plugin** — Linux accessibility API
- **D-Bus integration** — Desktop environment interaction
- **X11/Wayland input** — Keyboard/mouse simulation

---

## Medium-Term

### Firefox Extension

Extend the Chrome Extension pattern to Firefox:

- WebExtension API compatibility
- Same WebSocket communication pattern
- Cross-browser automation

### Event System

Real-time UI change notifications:

- UIA event subscriptions (focus, property change, structure change)
- CDP event listeners (DOM mutation, navigation)
- Push-based updates instead of polling
- Webhook/WebSocket delivery to agent runtimes

### Test Framework

Comprehensive test suite:

- Unit tests for each module with mocked PowerShell/CDP
- Integration tests with mock applications
- Plugin-specific tests with framework simulators
- CI/CD pipeline with automated testing

---

## Long-Term

### Compound Actions

Higher-level operations built on the primitive API:

- "Fill this form with this data" (smart field matching)
- "Navigate to this section" (menu/breadcrumb traversal)
- "Find the most relevant button" (semantic matching with LLM)

### Plugin SDK

Third-party plugin development:

- Plugin template generator
- Testing harness
- Plugin registry for community sharing

### Multi-Machine Coordination

Control apps across machines:

- Remote UAB instances
- Centralized orchestration
- Cross-machine action chains
- Agent-to-agent communication
