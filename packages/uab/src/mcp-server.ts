/**
 * UAB MCP Server — Exposes UAB as Model Context Protocol tools.
 *
 * When an AI agent connects via MCP, it discovers UAB tools natively —
 * no need to "decide" to use UAB over screenshots. The tools are just there.
 *
 * Implements MCP JSON-RPC over stdio (no external dependencies).
 *
 * @example
 * ```bash
 * # Add to claude_desktop_config.json or any MCP-compatible agent:
 * {
 *   "mcpServers": {
 *     "desktop-control": {
 *       "command": "node",
 *       "args": ["dist/uab/mcp-server.js"]
 *     }
 *   }
 * }
 * ```
 */

import { UABConnector } from './connector.js';
import type { ElementSelector } from './types.js';
import * as readline from 'readline';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

// Ensure working directory is the UAB repo root (not system32)
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const uabRoot = resolve(__dirname, '..');
try { process.chdir(uabRoot); } catch { /* best effort */ }

// ─── Shared RawViewWalker helpers ────────────────────────────────

interface RawElement {
  name: string;
  type: string;
  id: string;
  actions: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Generate PowerShell script that walks the full raw UIA tree via RawViewWalker */
function rawWalkerScript(pid: number): string {
  return `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)
$win = $rootEl.FindFirst([System.Windows.Automation.TreeScope]::Children, $procCond)
if (-not $win) { Write-Output '{"elements":[],"winX":0,"winY":0,"winW":0,"winH":0}'; exit }

$winRect = $win.Current.BoundingRectangle
$walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
$results = @()
$stack = New-Object System.Collections.Stack
$ch = $walker.GetFirstChild($win)
while ($ch -or $stack.Count -gt 0) {
  if ($ch) {
    try {
      $rawName = $ch.Current.Name
      $name = if ($rawName) { $rawName -replace '[^\\x20-\\x7E]', '' } else { '' }
      $controlType = $ch.Current.ControlType.ProgrammaticName -replace 'ControlType\\\\.', ''
      $rawId = $ch.Current.AutomationId
      $automationId = if ($rawId) { $rawId -replace '[^\\x20-\\x7E]', '' } else { '' }
      $rect = $ch.Current.BoundingRectangle

      $patterns = @()
      try {
        foreach ($p in $ch.GetSupportedPatterns()) {
          $pName = $p.ProgrammaticName -replace 'Identifiers\\\\.Pattern', '' -replace 'PatternIdentifiers\\\\.Pattern', ''
          $patterns += $pName
        }
      } catch {}

      if ($name -or $automationId) {
        $results += @{
          name = $name
          type = $controlType
          id = $automationId
          actions = ($patterns -join ',')
          x = if ($rect.X -gt -99999 -and $rect.X -lt 99999) { [int]$rect.X } else { 0 }
          y = if ($rect.Y -gt -99999 -and $rect.Y -lt 99999) { [int]$rect.Y } else { 0 }
          w = if ($rect.Width -gt 0 -and $rect.Width -lt 99999) { [int]$rect.Width } else { 0 }
          h = if ($rect.Height -gt 0 -and $rect.Height -lt 99999) { [int]$rect.Height } else { 0 }
        }
      }
    } catch {}
    $stack.Push($ch)
    $ch = $walker.GetFirstChild($ch)
  } else {
    $parent = $stack.Pop()
    $ch = $walker.GetNextSibling($parent)
  }
}

@{
  elements = $results
  winX = [int]$winRect.X
  winY = [int]$winRect.Y
  winW = [int]$winRect.Width
  winH = [int]$winRect.Height
} | ConvertTo-Json -Compress -Depth 3
`;
}

/** Deduplicate elements by type+name+bounds */
function dedup(elements: RawElement[]): RawElement[] {
  const seen = new Set<string>();
  const result: RawElement[] = [];
  for (const el of elements) {
    const key = `${el.type}|${el.name}|${el.x},${el.y},${el.w},${el.h}`;
    if (!seen.has(key)) {
      seen.add(key);
      result.push(el);
    }
  }
  return result;
}

/** Run RawViewWalker and return deduplicated elements + window bounds */
async function rawWalk(pid: number): Promise<{ elements: RawElement[]; winX: number; winY: number; winW: number; winH: number }> {
  const { runPSRawInteractive } = await import('./ps-exec.js');
  const raw = runPSRawInteractive(rawWalkerScript(pid), 30000);
  const data = JSON.parse(raw);
  let elements: RawElement[] = data.elements || [];
  if (!Array.isArray(elements)) elements = [elements];
  elements = dedup(elements);
  return { elements, winX: data.winX, winY: data.winY, winW: data.winW, winH: data.winH };
}

// ─── MCP Protocol Types ─────────────────────────────────────────

interface JsonRpcMessage {
  jsonrpc: '2.0';
  id?: number | string;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

interface MCPTool {
  name: string;
  description: string;
  inputSchema: {
    type: 'object';
    properties: Record<string, unknown>;
    required?: string[];
  };
}

interface MCPToolResult {
  content: Array<{ type: 'text'; text: string }>;
  isError?: boolean;
}

// ─── Tool Definitions ──────────────────────────────────────────

const TOOLS: MCPTool[] = [
  {
    name: 'desktop_scan',
    description: 'Scan for all running desktop applications. Returns app names, PIDs, and detected frameworks. IMPORTANT: Call this FIRST and store the result as your baseline — you need to know what was already running so you never close the user\'s apps. After connecting, call desktop_flow for interaction flows AND desktop_flow("hygiene-protocol") for system hygiene rules (window discipline, clipboard respect, cleanup). Tag every app you open as present_to_user (leave open) or tool_use_only (close when done).',
    inputSchema: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'desktop_apps',
    description: 'List previously discovered apps from the registry (instant, no scanning). Use after desktop_scan.',
    inputSchema: {
      type: 'object',
      properties: {
        framework: { type: 'string', description: 'Filter by framework (electron, qt, gtk, uwp, win32, etc.)' },
      },
    },
  },
  {
    name: 'desktop_connect',
    description: 'Connect to a desktop application by name or PID. Required before interacting with an app.',
    inputSchema: {
      type: 'object',
      properties: {
        target: { type: ['string', 'number'], description: 'App name (fuzzy match) or PID number' },
      },
      required: ['target'],
    },
  },
  {
    name: 'desktop_ui_tree',
    description: 'Get the structured UI element tree of a connected app. Returns buttons, text fields, menus, etc. with their IDs, types, labels, and bounding rectangles. MUCH faster and more reliable than taking a screenshot — use this instead.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID of the connected app' },
        maxDepth: { type: 'number', description: 'Max tree depth (default: 8)' },
      },
      required: ['pid'],
    },
  },
  {
    name: 'desktop_find_elements',
    description: 'Find specific UI elements by type, label, or properties. Returns matching elements with their IDs for use with desktop_act. More targeted than desktop_ui_tree.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
        type: { type: 'string', description: 'Element type: button, textfield, menu, menuitem, checkbox, tab, etc.' },
        label: { type: 'string', description: 'Text label to search for (substring match)' },
        labelExact: { type: 'string', description: 'Exact label match' },
        visible: { type: 'boolean', description: 'Only visible elements (default: true)' },
        enabled: { type: 'boolean', description: 'Only enabled elements' },
        limit: { type: 'number', description: 'Max results (default: 50)' },
      },
      required: ['pid'],
    },
  },
  {
    name: 'desktop_act',
    description: 'Perform an action on a UI element. Actions: click, doubleclick, type, clear, select, check, uncheck, toggle, expand, collapse, invoke, scroll, focus. Use element IDs from desktop_find_elements or desktop_ui_tree.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
        elementId: { type: 'string', description: 'Element ID from enumerate/query results' },
        action: { type: 'string', description: 'Action: click, doubleclick, type, clear, select, check, uncheck, toggle, expand, collapse, invoke, scroll, focus, hover' },
        text: { type: 'string', description: 'Text to type (for "type" action)' },
        value: { type: 'string', description: 'Value to set (for "select" action)' },
      },
      required: ['pid', 'elementId', 'action'],
    },
  },
  {
    name: 'desktop_spatial_map',
    description: 'Your EYES — call this after connecting and after every action to see the current state. Returns every button, link, input, and text element organized into rows and columns. Use WITH desktop_flow: the map shows what is on screen NOW, the flow tells you HOW to interact. After sending a message, call this again to see if new elements appeared (like response text or Copy buttons). Uses RawViewWalker to see everything including inner Electron web content.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
        format: { type: 'string', enum: ['detailed', 'compact', 'json'], description: 'Output format (default: compact)' },
        maxDepth: { type: 'number', description: 'Max tree depth (default: 6)' },
      },
      required: ['pid'],
    },
  },
  {
    name: 'desktop_state',
    description: 'Get current app state: window position/size, focused element, active modals, title. NOTE: Negative coordinates or coordinates >2000 are normal — they indicate the window is on a secondary monitor. Do NOT move windows based on coordinates alone. Multi-monitor setups have windows at positions like (1726,-1080) which are valid.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
      },
      required: ['pid'],
    },
  },
  {
    name: 'desktop_keypress',
    description: 'Send a keyboard key to an app. Keys: enter, tab, escape, space, backspace, delete, up, down, left, right, home, end, f1-f12, etc.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
        key: { type: 'string', description: 'Key name (e.g., "enter", "tab", "escape", "f5")' },
      },
      required: ['pid', 'key'],
    },
  },
  {
    name: 'desktop_hotkey',
    description: 'Send a keyboard shortcut to an app. Examples: "ctrl+s" (save), "ctrl+c" (copy), "alt+f4" (close), "ctrl+shift+p" (command palette).',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
        keys: { type: 'string', description: 'Key combination (e.g., "ctrl+s", "ctrl+shift+p", "alt+f4")' },
      },
      required: ['pid', 'keys'],
    },
  },
  {
    name: 'desktop_window',
    description: 'Control app window: minimize, maximize, restore, close, move, or resize. IMPORTANT: NEVER move or resize a window unless the user explicitly asks. Windows may be on secondary monitors (negative or large coordinates are normal). Moving a window disrupts the user\'s multi-monitor layout and snap positions.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
        action: { type: 'string', enum: ['minimize', 'maximize', 'restore', 'close', 'move', 'resize'], description: 'Window action' },
        x: { type: 'number', description: 'X position (for move/resize)' },
        y: { type: 'number', description: 'Y position (for move/resize)' },
        width: { type: 'number', description: 'Width (for resize)' },
        height: { type: 'number', description: 'Height (for resize)' },
      },
      required: ['pid', 'action'],
    },
  },
  {
    name: 'desktop_smart_click',
    description: 'Click an element by name using intelligent 6-method fallback. Tries: InvokePattern → Focus+Enter → Parent invoke → Click coordinates → Expand → Toggle. Use when you know the element name but not its ID.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
        name: { type: 'string', description: 'Element name/label to click' },
      },
      required: ['pid', 'name'],
    },
  },
  {
    name: 'desktop_chain',
    description: 'Execute a multi-step action sequence atomically (all steps in one PowerShell session, no focus stealing between steps). Steps: hotkey, keypress, click, type, wait.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
        steps: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              action: { type: 'string', enum: ['hotkey', 'keypress', 'click', 'type', 'wait'] },
              keys: { type: 'string', description: 'For hotkey: "ctrl+s"' },
              key: { type: 'string', description: 'For keypress: "enter"' },
              name: { type: 'string', description: 'For click: element name' },
              text: { type: 'string', description: 'For type: text to enter' },
              ms: { type: 'number', description: 'For wait: milliseconds' },
            },
            required: ['action'],
          },
          description: 'Steps to execute in sequence',
        },
      },
      required: ['pid', 'steps'],
    },
  },
  {
    name: 'desktop_focused',
    description: 'Get the currently focused UI element in an app. Fast (<50ms) — useful for tracking where the cursor/focus is.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
      },
      required: ['pid'],
    },
  },
  {
    name: 'desktop_flow',
    description: 'Get the learned interaction flow (procedural memory) for a specific app. Use this WITH desktop_spatial_map — the map is your EYES (what is on screen), the flow is your MEMORY (how to interact). Many Electron apps have hidden inputs that require specific navigation. The flow gives you the exact steps. After each action, call desktop_spatial_map again to verify the result. Workflow: spatial_map (see) → flow (plan) → execute → spatial_map (verify). Returns null if no flow exists.',
    inputSchema: {
      type: 'object',
      properties: {
        app: { type: 'string', description: 'App name (e.g., "grok", "chatgpt", "slack", "blender")' },
      },
      required: ['app'],
    },
  },
  {
    name: 'desktop_deep_query',
    description: 'X-ray vision: Returns ALL UI elements in an app using FindAll(TrueCondition). This finds EVERYTHING — including inner web content in Electron apps (buttons, links, inputs) that desktop_ui_tree and desktop_find_elements miss. Use this for Electron/web-based desktop apps like Grok, ChatGPT, Slack, VS Code. Returns element names, types, supported actions (InvokePattern, ValuePattern, etc.), and bounding rectangles. Filter by name or type to narrow results.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
        name: { type: 'string', description: 'Filter by element name (case-insensitive contains match)' },
        type: { type: 'string', description: 'Filter by element type (e.g., "Button", "Edit", "Text", "Hyperlink")' },
      },
      required: ['pid'],
    },
  },
  {
    name: 'desktop_invoke',
    description: 'Directly invoke/activate a named element found via desktop_deep_query. Finds the element by name and activates it using the best available method (InvokePattern, ExpandCollapsePattern, SetFocus+Enter, coordinate click). Specify occurrence to target the Nth match (e.g., "last" for the last Copy button). This is how you click buttons, links, and controls in Electron apps.',
    inputSchema: {
      type: 'object',
      properties: {
        pid: { type: 'number', description: 'Process ID' },
        name: { type: 'string', description: 'Element name to invoke (exact or partial match)' },
        occurrence: { type: 'string', description: 'Which match: "first" (default), "last", or a number like "2" for the 2nd match' },
      },
      required: ['pid', 'name'],
    },
  },
];

// ─── Server Implementation ─────────────────────────────────────

class UABMCPServer {
  private connector: UABConnector;
  private started = false;

  constructor() {
    this.connector = new UABConnector({ persistent: false });
  }

  async handleRequest(msg: JsonRpcMessage): Promise<JsonRpcMessage | null> {
    if (!msg.method) return null;

    try {
      switch (msg.method) {
        case 'initialize':
          return this.respond(msg.id, {
            protocolVersion: '2024-11-05',
            capabilities: { tools: {} },
            serverInfo: {
              name: 'uab-desktop-control',
              version: '1.0.0',
            },
          });

        case 'notifications/initialized':
          // Start connector on initialized notification
          if (!this.started) {
            await this.connector.start();
            this.started = true;
          }
          return null; // notifications don't get responses

        case 'tools/list':
          return this.respond(msg.id, { tools: TOOLS });

        case 'tools/call':
          return this.respond(msg.id, await this.callTool(msg.params as { name: string; arguments?: Record<string, unknown> }));

        case 'ping':
          return this.respond(msg.id, {});

        default:
          return this.respondError(msg.id, -32601, `Method not found: ${msg.method}`);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return this.respondError(msg.id, -32603, message);
    }
  }

  private async callTool(params: { name: string; arguments?: Record<string, unknown> }): Promise<MCPToolResult> {
    const { name, arguments: args = {} } = params;

    // Ensure connector is started
    if (!this.started) {
      await this.connector.start();
      this.started = true;
    }

    try {
      let result: unknown;

      switch (name) {
        case 'desktop_scan':
          result = await this.connector.scan();
          break;

        case 'desktop_apps': {
          const all = this.connector.apps();
          if (args.framework) {
            result = all.filter((a: { framework?: string }) => a.framework === args.framework);
          } else {
            result = all;
          }
          break;
        }

        case 'desktop_connect': {
          if (typeof args.target === 'number') {
            result = await this.connector.connect(args.target as number);
          } else {
            result = await this.connector.connect(String(args.target));
          }
          break;
        }

        case 'desktop_ui_tree':
          result = await this.connector.enumerate(args.pid as number, args.maxDepth as number | undefined);
          // Flatten for readability
          result = this.connector.flattenTree(result as any[], args.maxDepth as number || 8);
          break;

        case 'desktop_find_elements': {
          const selector: ElementSelector = {};
          if (args.type) selector.type = args.type as any;
          if (args.label) selector.label = args.label as string;
          if (args.labelExact) selector.labelExact = args.labelExact as string;
          if (args.visible !== undefined) selector.visible = args.visible as boolean;
          if (args.enabled !== undefined) selector.enabled = args.enabled as boolean;
          if (args.limit) selector.limit = args.limit as number;
          result = await this.connector.query(args.pid as number, selector);
          break;
        }

        case 'desktop_act': {
          const actParams: Record<string, unknown> = {};
          if (args.text) actParams.text = args.text;
          if (args.value) actParams.value = args.value;
          result = await this.connector.act(
            args.pid as number,
            args.elementId as string,
            args.action as any,
            Object.keys(actParams).length > 0 ? actParams as any : undefined,
          );
          break;
        }

        case 'desktop_spatial_map': {
          // RawViewWalker — sees EVERYTHING including Electron inner content
          try {
            const smPid = args.pid as number;
            const data = await rawWalk(smPid);
            const elements = data.elements;

            // Cluster into rows by Y-coordinate proximity (threshold: 15px)
            const threshold = 15;
            const sorted = [...elements].sort((a, b) => a.y - b.y);
            const rows: Array<{y: number; elements: RawElement[]}> = [];
            for (const el of sorted) {
              const cy = el.y + el.h / 2;
              const existingRow = rows.find(r => Math.abs(r.y - cy) < threshold);
              if (existingRow) {
                existingRow.elements.push(el);
              } else {
                rows.push({ y: cy, elements: [el] });
              }
            }
            // Sort elements within each row left-to-right
            for (const row of rows) {
              row.elements.sort((a, b) => a.x - b.x);
            }

            // Build compact text representation
            let text = `=== SPATIAL MAP (PID ${smPid}) — ${elements.length} elements, ${rows.length} rows ===\n`;
            text += `Window: ${data.winW}x${data.winH} at (${data.winX},${data.winY})\n\n`;
            for (let i = 0; i < rows.length; i++) {
              const row = rows[i];
              const items = row.elements.map(e => {
                const acts = e.actions ? ` [${e.actions}]` : '';
                return `${e.name} (${e.type}${acts})`;
              }).join(' | ');
              text += `Row ${i}: ${items}\n`;
            }
            result = text;
          } catch (err) {
            result = `Spatial map failed: ${err instanceof Error ? err.message : err}`;
          }
          break;
        }

        case 'desktop_state':
          result = await this.connector.state(args.pid as number);
          break;

        case 'desktop_keypress':
          result = await this.connector.keypress(args.pid as number, args.key as string);
          break;

        case 'desktop_hotkey':
          result = await this.connector.hotkey(args.pid as number, args.keys as string);
          break;

        case 'desktop_window': {
          const winParams: Record<string, unknown> = {};
          if (args.x !== undefined) winParams.x = args.x;
          if (args.y !== undefined) winParams.y = args.y;
          if (args.width !== undefined) winParams.width = args.width;
          if (args.height !== undefined) winParams.height = args.height;
          result = await this.connector.window(
            args.pid as number,
            args.action as any,
            Object.keys(winParams).length > 0 ? winParams as any : undefined,
          );
          break;
        }

        case 'desktop_smart_click':
          result = await this.connector.smartInvoke(args.pid as number, args.name as string);
          break;

        case 'desktop_chain':
          result = await this.connector.atomicChain({
            pid: args.pid as number,
            steps: args.steps as any[],
          });
          break;

        case 'desktop_focused':
          result = await this.connector.focused(args.pid as number);
          break;

        case 'desktop_flow': {
          const appName = (args.app as string).toLowerCase().replace(/[^a-z0-9_-]/g, '');
          try {
            const { readFileSync, readdirSync } = await import('fs');
            const { resolve: resolvePath } = await import('path');
            const flowDir = resolvePath(__dirname, '..', 'data', 'flow-library');
            const files = readdirSync(flowDir).filter(f => f.endsWith('.json'));
            // Find matching flow file
            const match = files.find(f => f.replace('.json', '').toLowerCase() === appName);
            if (match) {
              const flowData = JSON.parse(readFileSync(resolvePath(flowDir, match), 'utf-8'));
              result = flowData;
            } else {
              // List available flows
              const available = files.map(f => f.replace('.json', ''));
              result = { found: false, message: `No flow for "${appName}". Available flows: ${available.join(', ')}` };
            }
          } catch (err) {
            result = { found: false, error: `Flow lookup failed: ${err instanceof Error ? err.message : err}` };
          }
          break;
        }

        case 'desktop_deep_query': {
          // RawViewWalker — sees EVERYTHING, with optional filters
          const dqPid = args.pid as number;
          const nameFilter = (args.name as string) || '';
          const typeFilter = (args.type as string) || '';
          try {
            const data = await rawWalk(dqPid);
            let elements = data.elements;
            if (nameFilter) {
              const lower = nameFilter.toLowerCase();
              elements = elements.filter(e => e.name && e.name.toLowerCase().includes(lower));
            }
            if (typeFilter) {
              const lower = typeFilter.toLowerCase();
              elements = elements.filter(e => e.type && e.type.toLowerCase().includes(lower));
            }
            result = { pid: dqPid, count: elements.length, elements };
          } catch (err) {
            result = { error: `Deep query failed: ${err instanceof Error ? err.message : err}` };
          }
          break;
        }

        case 'desktop_invoke': {
          const { runPSRawInteractive: runPS } = await import('./ps-exec.js');
          const invPid = args.pid as number;
          const invName = (args.name as string).replace(/'/g, "''");
          const invOccurrence = (args.occurrence as string) || 'first';
          const invScript = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${invPid}
)
$win = $rootEl.FindFirst([System.Windows.Automation.TreeScope]::Children, $procCond)
if (-not $win) { Write-Output '{"success":false,"error":"window not found"}'; exit }

$nameCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::NameProperty, '${invName}'
)
$matches = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $nameCond)

if ($matches.Count -eq 0) {
  Write-Output '{"success":false,"error":"no elements named ${invName} found","count":0}'
  exit
}

$target = $null
$occurrence = '${invOccurrence}'
if ($occurrence -eq 'last') {
  $maxY = -999999
  foreach ($el in $matches) {
    $y = $el.Current.BoundingRectangle.Y
    if ($y -gt $maxY) { $maxY = $y; $target = $el }
  }
} elseif ($occurrence -eq 'first') {
  $minY = 999999
  foreach ($el in $matches) {
    $y = $el.Current.BoundingRectangle.Y
    if ($y -lt $minY) { $minY = $y; $target = $el }
  }
} else {
  $idx = [int]$occurrence
  if ($idx -lt $matches.Count) { $target = $matches[$idx] }
}

if (-not $target) {
  Write-Output '{"success":false,"error":"occurrence not found"}'
  exit
}

try {
  $invoked = $false
  try {
    $invokePattern = $target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $invokePattern.Invoke() | Out-Null
    $invoked = $true
  } catch {}

  if (-not $invoked) {
    try {
      $target.SetFocus() | Out-Null
      [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
      $invoked = $true
    } catch {}
  }

  if (-not $invoked) {
    try {
      $expandPattern = $target.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
      $expandPattern.Expand() | Out-Null
      $invoked = $true
    } catch {}
  }

  if (-not $invoked) {
    $rect = $target.Current.BoundingRectangle
    $cx = [int]($rect.X + $rect.Width / 2)
    $cy = [int]($rect.Y + $rect.Height / 2)
    Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public class ClickHelper {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);
}
"@
    [ClickHelper]::SetCursorPos($cx, $cy) | Out-Null
    [ClickHelper]::mouse_event(2, 0, 0, 0, 0) | Out-Null
    [ClickHelper]::mouse_event(4, 0, 0, 0, 0) | Out-Null
    $invoked = $true
  }

  Start-Sleep -Milliseconds 500
  $clipRaw = [System.Windows.Forms.Clipboard]::GetText()
  $rect = $target.Current.BoundingRectangle
  @{
    success = $true
    name = ($target.Current.Name -replace '[^\\x20-\\x7E]', '')
    type = $target.Current.ControlType.ProgrammaticName
    totalMatches = $matches.Count
    x = if ($rect.X -gt -99999 -and $rect.X -lt 99999) { [int]$rect.X } else { 0 }
    y = if ($rect.Y -gt -99999 -and $rect.Y -lt 99999) { [int]$rect.Y } else { 0 }
    clipboardLength = $clipRaw.Length
    clipboardText = if ($clipRaw.Length -gt 0) { $clipRaw -replace '[^\\x20-\\x7E\\r\\n]', '' } else { '' }
  } | ConvertTo-Json -Compress -Depth 2
} catch {
  @{
    success = $false
    error = ($_.Exception.Message -replace '[^\\x20-\\x7E]', '')
    totalMatches = if ($matches) { $matches.Count } else { 0 }
  } | ConvertTo-Json -Compress
}
`;
          try {
            let raw = runPS(invScript, 20000);
            // Strip any non-JSON prefix (e.g. "True\r\n" from PowerShell return values)
            const jsonStart = raw.indexOf('{');
            if (jsonStart > 0) raw = raw.substring(jsonStart);
            result = JSON.parse(raw.trim());
          } catch (err) {
            result = { success: false, error: `Invoke failed: ${err instanceof Error ? err.message : err}` };
          }
          break;
        }

        default:
          return { content: [{ type: 'text', text: `Unknown tool: ${name}` }], isError: true };
      }

      return {
        content: [{
          type: 'text',
          text: typeof result === 'string' ? result : JSON.stringify(result, null, 2),
        }],
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return { content: [{ type: 'text', text: `Error: ${message}` }], isError: true };
    }
  }

  private respond(id: number | string | undefined, result: unknown): JsonRpcMessage {
    return { jsonrpc: '2.0', id, result };
  }

  private respondError(id: number | string | undefined, code: number, message: string): JsonRpcMessage {
    return { jsonrpc: '2.0', id, error: { code, message } };
  }

  async shutdown(): Promise<void> {
    if (this.started) {
      await this.connector.stop();
      this.started = false;
    }
  }
}

// ─── Main: stdio transport ─────────────────────────────────────

async function main() {
  const server = new UABMCPServer();

  const rl = readline.createInterface({
    input: process.stdin,
    terminal: false,
  });

  // Read newline-delimited JSON-RPC messages from stdin
  rl.on('line', async (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;

    try {
      const msg: JsonRpcMessage = JSON.parse(trimmed);
      const response = await server.handleRequest(msg);
      if (response) {
        process.stdout.write(JSON.stringify(response) + '\n');
      }
    } catch {
      const error: JsonRpcMessage = {
        jsonrpc: '2.0',
        id: null as any,
        error: { code: -32700, message: 'Parse error' },
      };
      process.stdout.write(JSON.stringify(error) + '\n');
    }
  });

  // Graceful shutdown
  process.on('SIGINT', async () => {
    await server.shutdown();
    process.exit(0);
  });

  process.on('SIGTERM', async () => {
    await server.shutdown();
    process.exit(0);
  });

  rl.on('close', async () => {
    await server.shutdown();
    process.exit(0);
  });
}

main().catch((err) => {
  process.stderr.write(`UAB MCP Server fatal error: ${err}\n`);
  process.exit(1);
});
