/**
 * Agent Prompt Templates — Drop-in system instructions that teach ANY AI agent
 * to prefer UAB's structured APIs over screenshots.
 *
 * The key insight: agents default to screenshots because their training data
 * shows that approach. We override that instinct at the prompt level.
 *
 * @example
 * ```ts
 * import { getAgentPrompt } from './agent-prompt.js';
 *
 * // For Claude Code / MCP agents
 * const prompt = getAgentPrompt('mcp');
 *
 * // For CLI-based agents (Codex, custom)
 * const prompt = getAgentPrompt('cli');
 *
 * // For HTTP API agents
 * const prompt = getAgentPrompt('http', { port: 3100 });
 * ```
 */

export interface PromptOptions {
  /** HTTP server port (for 'http' mode). Default: 3100 */
  port?: number;
  /** Path to CLI entry point. Default: 'node dist/uab/cli.js' */
  cliPath?: string;
  /** Include the full tool reference. Default: true */
  includeReference?: boolean;
  /** Include examples. Default: true */
  includeExamples?: boolean;
}

// ─── Core Philosophy Prompt ────────────────────────────────────

const CORE_PHILOSOPHY = `
## Desktop App Control — Universal App Bridge (UAB)

You have access to structured desktop app control tools that give you DIRECT access to application UI elements (buttons, text fields, menus, etc.) with their types, labels, positions, and available actions.

### CRITICAL: Do NOT default to screenshots for UI understanding.

Screenshots are:
- **Slow** (~2-5 seconds for capture + vision API processing)
- **Unreliable** (OCR errors, resolution issues, overlapping elements)
- **Expensive** (vision API tokens cost 10-100x more than text)
- **Lossy** (you can't click on pixels — you need element IDs anyway)

Instead, use structured UI queries which are:
- **Fast** (~50-200ms for full UI tree)
- **Accurate** (exact element types, labels, and IDs)
- **Actionable** (element IDs map directly to actions)
- **Cheap** (small JSON responses, no vision API needed)

### Decision Flow:
1. **Need to understand UI layout?** → Use spatial map (structured rows/columns)
2. **Need to find a specific element?** → Use find/query with type + label
3. **Need to click something?** → Use smart click (6-method fallback)
4. **Need to read text content?** → Use UI tree (text values included)
5. **Need to see images/charts?** → ONLY THEN use screenshot (last resort)
`.trim();

// ─── MCP-Specific Prompt ───────────────────────────────────────

const MCP_TOOLS_GUIDE = `
### Available Desktop Control Tools:

| Tool | What it does | When to use |
|------|-------------|-------------|
| \`desktop_scan\` | Discover running apps | First step — find what's available |
| \`desktop_connect\` | Connect to an app | Before any interaction |
| \`desktop_find_elements\` | Find UI elements by type/label | Looking for specific buttons, fields, etc. |
| \`desktop_spatial_map\` | Get visual layout as text | Understanding UI structure (INSTEAD of screenshot) |
| \`desktop_act\` | Click, type, toggle elements | Performing actions |
| \`desktop_smart_click\` | Click by name with fallback | When you know the label but not the ID |
| \`desktop_hotkey\` | Send keyboard shortcuts | Ctrl+S, Alt+F4, etc. |
| \`desktop_chain\` | Multi-step atomic sequences | Complex workflows (menu navigation, form filling) |
| \`desktop_state\` | Window position, focused element | Checking app state |
| \`desktop_focused\` | Currently focused element | Tracking cursor position |
| \`desktop_screenshot\` | Screenshot (LAST RESORT) | Only for visual content (images, charts) |
`.trim();

// ─── CLI-Specific Prompt ───────────────────────────────────────

function cliToolsGuide(cliPath: string): string {
  return `
### Desktop Control via CLI (all output is JSON):

\`\`\`bash
# Discovery
${cliPath} scan                              # Find all running apps
${cliPath} apps                              # List cached apps
${cliPath} find "notepad"                    # Search by name

# Connection
${cliPath} connect "Notepad"                 # Connect by name
${cliPath} connect 12345                     # Connect by PID

# UI Understanding (USE THESE instead of screenshots!)
${cliPath} map <pid>                         # Spatial layout (rows/columns)
${cliPath} map <pid> --format detailed       # With element details
${cliPath} enumerate <pid>                   # Full UI tree
${cliPath} query <pid> --type button         # Find buttons
${cliPath} query <pid> --label "Save"        # Find by label

# Actions
${cliPath} act <pid> <elementId> click       # Click element
${cliPath} act <pid> <elementId> type --text "hello"  # Type text
${cliPath} keypress <pid> enter              # Send key
${cliPath} hotkey <pid> ctrl+s               # Send shortcut

# State
${cliPath} state <pid>                       # App state
\`\`\`
`.trim();
}

// ─── HTTP API Prompt ───────────────────────────────────────────

function httpToolsGuide(port: number): string {
  return `
### Desktop Control via HTTP API (http://127.0.0.1:${port}):

\`\`\`
POST /scan          {}                                    # Discover apps
POST /connect       { "target": "Notepad" }               # Connect
POST /find          { "query": "chrome" }                  # Search

POST /enumerate     { "pid": 1234 }                       # UI tree
POST /query         { "pid": 1234, "selector": { "type": "button" } }
POST /spatial-map   { "pid": 1234 }                       # Structured layout
POST /text-map      { "pid": 1234, "format": "compact" }  # Text layout

POST /act           { "pid": 1234, "elementId": "btn_1", "action": "click" }
POST /state         { "pid": 1234 }                       # App state
\`\`\`
`.trim();
}

// ─── Examples ──────────────────────────────────────────────────

const EXAMPLES = `
### Example: Open a file in Notepad (GOOD vs BAD)

**BAD (screenshot-based):**
1. Take screenshot of Notepad
2. Send to vision API: "where is the File menu?"
3. Get coordinates (200, 30)
4. Click at coordinates (200, 30)
5. Take another screenshot
6. Send to vision API: "where is Open?"
7. Click at coordinates...
→ 6+ API calls, ~15 seconds, fragile

**GOOD (structured API):**
1. \`desktop_smart_click("File")\`
2. \`desktop_smart_click("Open")\`
→ 2 calls, ~400ms, reliable

### Example: Fill out a form

**BAD:** Screenshot → find each field → click coordinates → type
**GOOD:**
\`\`\`
desktop_chain([
  { action: "click", name: "Name" },
  { action: "type", text: "John Doe" },
  { action: "click", name: "Email" },
  { action: "type", text: "john@example.com" },
  { action: "click", name: "Submit" }
])
\`\`\`
→ Single atomic operation, no focus stealing between steps
`.trim();

// ─── Public API ────────────────────────────────────────────────

export type PromptMode = 'mcp' | 'cli' | 'http' | 'core';

/**
 * Get a system prompt that teaches an AI agent to use UAB instead of screenshots.
 *
 * @param mode - Integration mode: 'mcp' (tools), 'cli' (bash), 'http' (REST API), 'core' (philosophy only)
 * @param options - Customization options
 * @returns System prompt string to prepend to agent instructions
 */
export function getAgentPrompt(mode: PromptMode = 'mcp', options: PromptOptions = {}): string {
  const {
    port = 3100,
    cliPath = 'node dist/uab/cli.js',
    includeReference = true,
    includeExamples = true,
  } = options;

  const parts: string[] = [CORE_PHILOSOPHY];

  if (includeReference) {
    switch (mode) {
      case 'mcp':
        parts.push(MCP_TOOLS_GUIDE);
        break;
      case 'cli':
        parts.push(cliToolsGuide(cliPath));
        break;
      case 'http':
        parts.push(httpToolsGuide(port));
        break;
      case 'core':
        // Philosophy only, no specific tool reference
        break;
    }
  }

  if (includeExamples) {
    parts.push(EXAMPLES);
  }

  return parts.join('\n\n');
}

/**
 * Get a CLAUDE.md-compatible snippet for any project that wants desktop control.
 * Drop this into a CLAUDE.md file and agents will automatically prefer UAB.
 */
export function getClaudeMdSnippet(cliPath = 'node dist/uab/cli.js'): string {
  return `
## Desktop App Control (UAB)

You have access to the Universal App Bridge (UAB) CLI for controlling desktop applications.
UAB CLI: \`${cliPath} <command>\`

**Commands:** scan, connect, enumerate, query, act, state, keypress, hotkey, window, screenshot, map, find, chain

**All output is JSON.** Use \`map <pid>\` for spatial UI layout (FASTER and MORE RELIABLE than screenshots).
Only use \`screenshot <pid>\` as a last resort for visual-only content like images or charts.

**Quick workflow:**
1. \`${cliPath} scan\` — discover apps
2. \`${cliPath} connect <name>\` — connect to an app
3. \`${cliPath} map <pid>\` — understand the UI layout
4. \`${cliPath} query <pid> --type button --label "Save"\` — find elements
5. \`${cliPath} act <pid> <elementId> click\` — interact
`.trim();
}

/**
 * Get an MCP server configuration snippet for claude_desktop_config.json
 */
export function getMcpConfig(serverPath = 'dist/uab/mcp-server.js'): object {
  return {
    mcpServers: {
      'desktop-control': {
        command: 'node',
        args: [serverPath],
      },
    },
  };
}
