/**
 * Skill Template Generator
 *
 * Generates the SKILL.md file in Claude Code's plugin skill format.
 *
 * Two modes:
 * 1. Direct HTTP — for Claude Code CLI (runs on host, can reach localhost)
 * 2. Extension relay — for Co-work (runs in VM, talks through Chrome extension)
 *
 * The template includes both methods. Claude picks the one that works
 * from its current context.
 */

import { readFileSync } from 'fs';
import { resolve } from 'path';

function getVersion(): string {
  try {
    const pkg = JSON.parse(readFileSync(resolve('package.json'), 'utf-8'));
    return pkg.version || '0.9.0';
  } catch {
    return '0.9.0';
  }
}

export interface SkillTemplateOptions {
  /** Host IP that VMs can reach (e.g., 172.26.224.1) */
  hostIp: string;
  /** API key for authenticated requests */
  apiKey: string;
  /** Chrome extension ID (for runtime.sendMessage) */
  extensionId?: string;
}

export function generateSkillContent(options: SkillTemplateOptions): string {
  const version = getVersion();
  const date = new Date().toISOString().split('T')[0];
  const { hostIp, apiKey, extensionId } = options;
  const directBase = `http://${hostIp}:3100`;
  const localBase = 'http://localhost:3100';

  return `---
name: uab-bridge
description: >
  Use this skill when the user wants to interact with desktop applications,
  control GUI apps, automate Windows programs, read from or write to Excel/Word/PowerPoint,
  interact with browser tabs, click buttons, type text into fields, take screenshots of app windows,
  or perform any desktop automation task. Provides native control of desktop applications
  via UAB (Universal App Bridge).
version: ${version}
allowed-tools: [Bash, WebFetch]
---

<!-- UAB Bridge v${version} — installed ${date} -->

# UAB — Universal App Bridge

UAB gives you native control of desktop applications on this computer.
It runs on the host machine and accepts commands via HTTP.

Use UAB when the user wants you to interact with desktop applications
directly (Excel, Notepad, Chrome, Edge, Slack, Discord, Obsidian,
VS Code, Teams, and any other Windows application).

UAB detects the best control method for each application automatically.
You do not need to choose a method. Just call the endpoint.

## How to Connect

Try these methods in order. Use the first one that works.

### Method 1: Direct HTTP (fastest — works from host / Claude Code CLI)

\`\`\`bash
curl -s -X POST ${localBase}/scan -H "X-API-Key: ${apiKey}"
\`\`\`

If that fails with "connection refused", try the host gateway IP:

\`\`\`bash
curl -s -X POST ${directBase}/scan -H "X-API-Key: ${apiKey}"
\`\`\`

### Method 2: Health check (test if UAB is reachable)

\`\`\`bash
curl -s ${localBase}/health
# or
curl -s ${directBase}/health
\`\`\`

If health responds with \`{"status":"ok"}\`, use direct HTTP.
If both fail, UABServer may not be running on the host.

## Authentication

All POST requests MUST include the API key header:
\`\`\`
-H "X-API-Key: ${apiKey}"
\`\`\`

GET /health does not require authentication.

## API Reference

All calls are POST to the UAB server. Body is JSON.

### Scan all running applications

\`\`\`bash
curl -s -X POST ${localBase}/scan -H "X-API-Key: ${apiKey}"
\`\`\`

### Find an application by name

\`\`\`bash
curl -s -X POST ${localBase}/find \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "notepad"}'
\`\`\`

### Connect to an application

\`\`\`bash
curl -s -X POST ${localBase}/connect \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"target": "notepad"}'
# or by PID:
  -d '{"target": 1234}'
\`\`\`

### List UI elements

\`\`\`bash
curl -s -X POST ${localBase}/enumerate \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"pid": 1234}'
\`\`\`

### Find specific elements

\`\`\`bash
curl -s -X POST ${localBase}/query \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"pid": 1234, "selector": {"type": "button", "name": "Save"}}'
\`\`\`

### Click, type, and interact

\`\`\`bash
# Click a button
curl -s -X POST ${localBase}/act \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"pid": 1234, "elementId": "btn_1", "action": "click"}'

# Type text
curl -s -X POST ${localBase}/act \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"pid": 1234, "elementId": "input_1", "action": "type", "params": {"text": "Hello World"}}'

# Keyboard shortcut
curl -s -X POST ${localBase}/keypress \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"pid": 1234, "key": "ctrl+s"}'
\`\`\`

### Get application state and screenshots

\`\`\`bash
curl -s -X POST ${localBase}/state \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"pid": 1234}'

curl -s -X POST ${localBase}/screenshot \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"pid": 1234}'
\`\`\`

## Typical Workflow

1. \`/scan\` or \`/find\` to discover the app
2. \`/connect\` with the pid
3. \`/enumerate\` to see UI elements
4. \`/act\` to interact (click, type, etc.)
5. \`/disconnect\` when done

## Supported Applications

Microsoft Office (Excel, Word, PowerPoint) — framework-level COM control
Chrome, Edge, Brave, Vivaldi — Chrome DevTools Protocol via extension bridge
Electron apps (VS Code, Slack, Discord, Notion, Obsidian, Teams) — CDP
Qt apps (VLC, Telegram, OBS Studio, VirtualBox, Wireshark) — UI Automation
Any other Windows application — Windows UI Automation fallback

## Important Notes

Always call /connect before /enumerate, /query, /act, or /state.
Always include the X-API-Key header in every POST request.
If UABServer is not responding, it may need to be restarted on the host.
If localhost doesn't work, try ${directBase} instead.
`;
}
