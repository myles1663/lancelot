# Design Decisions

This document records key architectural decisions made during UAB's development, the alternatives considered, and the rationale for each choice.

---

## 1. Smart Function Discovery as Core Architecture

**Decision:** Make Smart Function Discovery — the five-phase pipeline (scan → identify → register → connect → learn) — the central organizing principle of UAB, not just an optional feature.

**Alternatives Considered:**
- **Static configuration:** Require users to specify app names, PIDs, and frameworks manually. Simpler but defeats the purpose of agent automation.
- **Plugin-only discovery:** Let each plugin discover apps independently. Creates fragmented, inconsistent detection.
- **Central detection, no learning:** Detect frameworks but don't remember results. Works but requires re-scanning every time.

**Rationale:** AI agents need to discover and control apps without human configuration. The five-phase pipeline gives agents a complete "scan the world, understand it, remember it, control it" capability. The learning loop (phase 5) means each interaction makes the next one faster and smarter. This is what differentiates UAB from traditional automation tools.

---

## 2. DLL Scanning for Framework Detection

**Decision:** Identify UI frameworks by scanning loaded DLL modules via PowerShell.

**Alternatives Considered:**
- **Window class names:** Windows registers class names like `Qt5QWindowIcon` or `GtkWindow`, but these are inconsistent across versions and configurations.
- **File path heuristics:** Check for `node_modules/electron` or `app.asar` in the app directory. Works for Electron but not portable to other frameworks.
- **Process command-line parsing:** Check for flags like `--type=renderer`. Too fragile — varies by app.
- **Binary analysis:** Parse PE headers for imports. More complex, slower, and doesn't add significant accuracy over DLL scanning.

**Rationale:** DLL scanning is deterministic. An Electron app _always_ loads `chrome_elf.dll`. A Qt6 app _always_ loads `qt6core.dll`. The mapping from DLL → framework is stable across versions. A single PowerShell call can scan all processes at once, making it fast even on systems with hundreds of running apps.

---

## 3. Batch Processing for Performance

**Decision:** Batch all system scans (process enumeration, DLL scanning, window title fetching) into minimal PowerShell calls.

**Alternatives Considered:**
- **Per-process scanning:** Call PowerShell once per PID for modules. Simple but ~150 calls × 200ms = 30 seconds.
- **Native Node.js module scanning:** Use N-API bindings for direct Win32 access. Fast but requires native compilation.
- **Background scanning:** Scan continuously in the background. Adds complexity and resource usage.

**Rationale:** The batching approach transforms 150+ PowerShell calls into 3-5 calls:
1. One WMI call for all processes
2. Batches of 50 PIDs for module scanning
3. One P/Invoke call for all window titles

Result: Full system scan in 2-5 seconds instead of 30+ seconds. This is fast enough for interactive use while simple enough to maintain.

---

## 4. Dual-Indexed In-Memory Maps

**Decision:** Store app profiles in two coordinated Maps: `Map<executable, AppProfile>` and `Map<pid, executable>`.

**Alternatives Considered:**
- **Single Map by PID:** Simple but PIDs are ephemeral — they change on app restart.
- **Single Map by name:** Name collisions are possible (multiple Chrome instances).
- **Array with linear search:** Simple but O(n) for every lookup.
- **SQLite database:** More query-capable but adds a dependency and is overkill for ~100 profiles.

**Rationale:** The executable name (e.g., `code.exe`) is the stable, unique key. PIDs are ephemeral but essential for real-time connections. The dual-index gives:
- O(1) lookup by executable name (the stable key)
- O(1) lookup by PID (needed for `query()`, `act()`, etc.)
- No duplication (PID index just maps to the executable key)

The trade-off of maintaining two Maps is minimal compared to the performance benefit.

---

## 5. JSON Profile Persistence

**Decision:** Persist the App Registry in a single JSON file (`registry.json`) instead of a database.

**Alternatives Considered:**
- **SQLite:** More query-capable but adds a native dependency (`better-sqlite3`) and is overkill for ~100 profiles.
- **No persistence:** Simpler, but forces a full 2-5 second scan on every CLI invocation.
- **Multiple JSON files (one per app):** Fine-grained updates but creates file sprawl.
- **YAML/TOML:** More human-readable but less universal than JSON.

**Rationale:** A single JSON file is:
- **Fast enough** — Reading/writing 100 profiles takes <1ms
- **Git-friendly** — Single file with readable diffs
- **Dependency-free** — No native modules
- **Human-inspectable** — Developers can read and edit directly
- **Cross-session** — Survives process restarts

The auto-save on mutation (with deferred save for bulk operations) means the file stays in sync without explicit save calls.

---

## 6. Framework-Independent Connector Pattern

**Decision:** Add `UABConnector` as an instantiable, dependency-free API layer above the singleton `UABService`.

**Alternatives Considered:**
- **Singleton only:** Simpler, but only one consumer can use UAB at a time.
- **Factory function with shared state:** Complex lifecycle management.
- **Microservice architecture:** Over-engineered for local agent integration.

**Rationale:** The Connector pattern satisfies two requirements:
1. **Agent-framework independence** — No imports of Grammy, SQLite, or any agent runtime. Any agent (Claude Code, Codex, custom bots) can use `new UABConnector()`.
2. **Multiple instances** — Different scripts can each have their own connector, sharing the same registry via JSON persistence.

The connector is the **primary API** — it's what the CLI and library users interact with. The singleton `UABService` remains for single-consumer scenarios.

---

## 7. Plugin Architecture with Ordered Priority

**Decision:** Framework plugins are registered in a fixed priority order. The first plugin where `canHandle(app) === true` wins.

**Alternatives Considered:**
- **Capability negotiation:** Each plugin reports capabilities, system picks best. More complex without clear benefit.
- **User selection:** Let agents choose plugins. Breaks zero-configuration principle.
- **Score-based selection:** Plugins return capability scores. Over-engineered.

**Rationale:** Simple, predictable, debuggable. The priority order reflects a clear hierarchy: extension bridge → CDP → framework-specific → UIA (universal fallback). Adding a new plugin means inserting it at the right position.

---

## 8. Learning Loop (Registry Updates After Connection)

**Decision:** After each successful connection, update the registry with the control method that worked.

**Alternatives Considered:**
- **No learning:** Always try the full cascade from scratch. Predictable but slow for repeated connections.
- **Explicit user configuration:** Let users set preferred methods per app. Not agent-friendly.
- **Heuristic-only selection:** Choose method based on framework without feedback. Missing the opportunity to learn from real results.

**Rationale:** The learning loop is simple but powerful:
- `connect("excel")` → OfficePlugin succeeds → store `preferredMethod: 'com+uia'`
- Next `connect("excel")` → try COM+UIA first → skip the full cascade

This is the "learn" phase of Smart Function Discovery. Each connection makes the next one faster. The registry persists this knowledge across sessions, so UAB gets smarter over time.

---

## 9. Three-Tier Cache with Action-Triggered Invalidation

**Decision:** Cache element trees (5s), query results (3s), and app state (2s) separately, with automatic invalidation on mutating actions.

**Alternatives Considered:**
- **No caching:** Simple but slow (100-500ms per UI tree enumeration).
- **Single unified cache:** Can't tune TTLs per operation.
- **Event-based invalidation:** More accurate but unreliable across frameworks.
- **Time-only TTL:** Can return stale data after mutations.

**Rationale:** Matches usage patterns:
- Tree structure changes slowly (5s is safe)
- Query results change when actions occur (3s + action invalidation)
- App state changes frequently (2s)

The action-triggered invalidation is the key insight: after `click` or `type`, the cache must be cleared because the UI probably changed. After `focus` or `scroll`, the cache can stay.

---

## 10. PowerShell as the Windows API Bridge

**Decision:** Use PowerShell subprocesses to access Windows APIs (UIA, COM, P/Invoke) from Node.js.

**Alternatives Considered:**
- **Native Node.js addons (N-API/node-ffi):** Fast but requires compilation, breaks across Node versions.
- **Edge.js / .NET interop:** Heavy dependency.
- **Python subprocess:** Adds Python runtime dependency.

**Rationale:** PowerShell is pre-installed on every Windows system. It has direct access to:
- `System.Windows.Automation` (UIA)
- COM objects (Office)
- Win32 APIs (P/Invoke)
- .NET types

No compilation, no native addons, no external dependencies. The ~200ms startup latency is mitigated by caching.

---

## 11. Temp-File PowerShell Execution

**Decision:** Write PowerShell scripts to temp files and execute them, not inline commands.

**Alternatives Considered:**
- **Inline commands:** Hits 8,191-character Windows command-line limit.
- **Stdin piping:** Unreliable with complex scripts.
- **Persistent process:** Complex lifecycle management.

**Rationale:** No length limit, no escaping issues, clean error handling. The ~50ms file write cost is negligible vs. PowerShell's ~200ms startup.

---

## 12. Chrome Extension over CDP-Only

**Decision:** Ship a Chrome Extension (Manifest V3) for browser control, with CDP as fallback.

**Alternatives Considered:**
- **CDP only:** Requires browser relaunch with debug flag.
- **Puppeteer/Playwright:** Launches new browser instances.
- **Accessibility APIs only:** Can't access cookies, storage, or execute JavaScript.

**Rationale:** Users have active browser sessions. Asking them to relaunch is unacceptable for a personal AI assistant. The extension connects via WebSocket to the already-running browser.

---

## 13. JSON-Only CLI Output

**Decision:** The CLI outputs pure JSON for every command.

**Alternatives Considered:**
- **Human-readable tables:** Hard to parse programmatically.
- **Dual mode (--json flag):** Doubles testing surface.

**Rationale:** The CLI is for AI agents, not humans. Agents parse JSON natively. Human operators use the library API or Telegram commands.

---

## 14. Rate Limiting at the Permission Layer

**Decision:** Rate limit at 100 actions/minute per PID in the PermissionManager.

**Alternatives Considered:**
- **No rate limiting:** A bug could spam hundreds of actions per second.
- **Global rate limit:** Unfair to multi-app workflows.

**Rationale:** Per-PID rate limiting prevents runaway automation while allowing normal multi-app workflows. 100/minute is generous (average agent: ~10/minute) while catching infinite loops.

---

## 15. Co-work Bridge via Chrome Extension Relay

**Decision:** Route Co-work bridge traffic through the Chrome extension instead of exposing UABServer on a network port that VMs can reach directly.

**Alternatives Considered:**
- **Direct network port:** Expose UABServer on a port reachable from VMs. Simple but opens new attack surface and requires firewall changes.
- **SSH tunnel:** Forward a port over SSH. Adds setup complexity for each VM.
- **Shared file system:** Communicate via files on a shared drive. Too slow and unreliable for real-time control.

**Rationale:** Co-work already trusts the Chrome extension. The extension runs on the host and can reach localhost:3100. This avoids opening new ports and leverages existing trust relationships. The relay path is: Co-work VM → Chrome extension (trusted) → localhost:3100 (UABServer). No new trust boundaries are crossed, no new ports are exposed, and the existing Chrome extension WebSocket infrastructure handles the communication.

---

## 17. Recursive Application Bridge (Flow Library)

**Decision**: Store learned app interaction sequences in a JSON library and serve them via API endpoints.

**Alternatives considered**:
- Hardcode sequences in the skill file → doesn't scale, requires skill updates for every new app
- Let agents figure it out every time → slow, unreliable, wastes tokens re-reasoning known solutions
- Use a database with vector search → overengineered for <100 flows, adds complexity

**Rationale**: Each app has unique UI quirks (Tab counts, input activation, clipboard vs SendKeys). These are discovered once through real interaction and stored permanently. The flow library creates a recursive improvement loop where the system gets better at controlling apps the more it controls them. This is the key architectural differentiator — UAB builds procedural memory from interaction, unlike other automation tools that require manual configuration per app.

---

## 18. BSL 1.1 with Apache 2.0 Conversion

**Decision:** Business Source License 1.1, converting to Apache 2.0 four years after each release.

**Rationale:** Protects IP while keeping code open for inspection, personal use, and academic research. The four-year conversion ensures eventual full open source.
