# Universal App Bridge (UAB)

**Framework-level desktop app control for AI agents.**

Hook into UI frameworks to get structured, reliable access to any desktop application's interface — no cooperation from app developers required.

## The Problem

AI agents need to control local applications, but most apps expose no programmatic interface. Accessibility APIs are unreliable, vision+click is slow and brittle. UAB hooks at the **UI toolkit level** — intercepting the framework's own introspection and debug capabilities for agent control.

## Supported Frameworks

| Framework | Plugin | Apps Covered |
|-----------|--------|-------------|
| **Electron** | Chrome DevTools Protocol | VS Code, Slack, Discord, Notion, Obsidian, Spotify, Teams |
| **Qt 5/6** | UIA Bridge | VLC, Telegram Desktop, OBS Studio, VirtualBox, Wireshark |
| **GTK 3/4** | UIA Bridge | GIMP, Inkscape, GNOME apps |
| **WPF/.NET** | Windows UI Automation | Windows enterprise apps, Visual Studio |
| **Flutter** | UIA Bridge | Google apps, Ubuntu desktop apps |
| **Java Swing/FX** | JAB→UIA Bridge | JetBrains IDEs, Android Studio |
| **MS Office** | COM Automation | Word, Excel, PowerPoint, Outlook |
| **Win32** | Windows UI Automation | Universal fallback for any Windows app |

## Quick Start

### As a Library

```typescript
import { uab } from 'universal-app-bridge';

// Start the service
await uab.start();

// Discover running apps
const apps = await uab.detect();
console.log(apps);
// [{ pid: 1234, name: 'Slack', framework: 'electron', confidence: 0.9 }]

// Connect to an app
await uab.connect(apps[0]);

// Find all buttons
const buttons = await uab.query(apps[0].pid, { type: 'button' });

// Click one
await uab.act(apps[0].pid, buttons[0].id, 'click');

// Get app state
const state = await uab.state(apps[0].pid);

// Cleanup
await uab.stop();
```

### As a CLI (for AI agents)

The CLI outputs pure JSON, designed for Claude/GPT/any AI agent calling via bash:

```bash
# Scan for controllable apps
uab detect

# Connect and enumerate UI
uab connect Slack
uab enumerate 1234

# Find specific elements
uab query 1234 --type button --label "Send"

# Perform actions
uab act 1234 btn_42 click
uab act 1234 input_7 type --text "Hello world"

# Keyboard input
uab keypress 1234 Enter
uab hotkey 1234 ctrl+s

# Window management
uab window 1234 maximize
uab screenshot 1234 --output screen.png

# Get app state
uab state 1234
```

## Unified API

Every framework plugin maps its native UI tree into the same types:

### `uab.detect()` — Discover Apps

```typescript
const apps: DetectedApp[] = await uab.detect();
// { pid, name, path, framework, confidence, windowTitle }
```

### `uab.enumerate(pid)` — List UI Elements

```typescript
const elements: UIElement[] = await uab.enumerate(pid);
// Each element has: id, type, label, properties, bounds, children, actions, visible, enabled
```

### `uab.query(pid, selector)` — Search Elements

```typescript
// By type
const buttons = await uab.query(pid, { type: 'button' });

// By label (fuzzy match)
const submit = await uab.query(pid, { label: 'Submit' });

// Combined
const sendBtn = await uab.query(pid, { type: 'button', label: 'Send' });

// With constraints
const visible = await uab.query(pid, { type: 'textfield', visible: true, limit: 5 });
```

### `uab.act(pid, elementId, action, params?)` — Perform Actions

```typescript
await uab.act(pid, 'btn_1', 'click');
await uab.act(pid, 'input_3', 'type', { text: 'Hello' });
await uab.act(pid, 'select_5', 'select', { value: 'Option A' });
await uab.act(pid, 'elem_2', 'scroll', { direction: 'down', amount: 3 });
```

**Supported actions:** `click`, `doubleclick`, `rightclick`, `type`, `clear`, `select`, `scroll`, `focus`, `hover`, `expand`, `collapse`, `invoke`, `check`, `uncheck`, `toggle`, `keypress`, `hotkey`, `minimize`, `maximize`, `restore`, `close`, `move`, `resize`, `screenshot`

### `uab.state(pid)` — Get App State

```typescript
const state: AppState = await uab.state(pid);
// { window: { title, size, position, focused }, activeElement, modals, menus }
```

## Advanced Features

### Action Chains

Multi-step workflows with verification between steps:

```typescript
import { ChainExecutor } from 'universal-app-bridge';

const chain = {
  name: 'fill-form',
  steps: [
    { type: 'action', elementId: 'name_input', action: 'type', params: { text: 'John' } },
    { type: 'action', elementId: 'email_input', action: 'type', params: { text: 'john@example.com' } },
    { type: 'wait', selector: { type: 'button', label: 'Submit' }, timeoutMs: 5000 },
    { type: 'action', elementId: 'submit_btn', action: 'click' },
  ],
};

const result = await uab.executeChain(chain);
```

### Control Router (Fallback Strategy)

UAB automatically selects the best control method with fallback:

```
Priority 1: Direct API / Framework Hook (Electron CDP, etc.)
Priority 2: Windows UI Automation (universal fallback)
Priority 3: Accessibility API
Priority 4: Vision + Input Injection
```

### Smart Caching

Element trees are cached with intelligent invalidation:
- Tree cache: 5s TTL per PID
- Query cache: 3s TTL, max 50 per PID
- Automatic invalidation on mutating actions (click, type, etc.)

### Permission & Safety Model

Built-in safety controls for agent use:
- **Risk levels:** safe, moderate, destructive
- **Rate limiting:** 100 actions/min per PID (configurable)
- **Audit log:** Last 1000 actions with timestamps
- **Destructive action gating:** `close` requires confirmation

### Health Monitoring

Connection lifecycle management:
- 30-second health check intervals
- Auto-reconnect with exponential backoff (1s → 2s → 4s → 8s max)
- Stale connection cleanup after 5 minutes of failure
- Event callbacks for connection state changes

## Element Types

UAB normalizes all framework-specific element types into a unified set:

`window`, `button`, `textfield`, `textarea`, `checkbox`, `radio`, `select`, `menu`, `menuitem`, `list`, `listitem`, `table`, `tablerow`, `tablecell`, `tab`, `tabpanel`, `tree`, `treeitem`, `slider`, `progressbar`, `scrollbar`, `toolbar`, `statusbar`, `dialog`, `tooltip`, `image`, `link`, `label`, `heading`, `separator`, `container`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UAB_LOG_LEVEL` | `info` | Log level: `debug`, `info`, `warn`, `error` |
| `UAB_LOG_FILE` | _(none)_ | Optional file path for log output |
| `LOG_LEVEL` | `info` | Fallback log level (if UAB_LOG_LEVEL not set) |

## Requirements

- **Node.js** >= 18.0.0
- **Windows** (primary platform — UIA, COM, PowerShell)
- Linux/macOS support via framework-specific plugins

## Architecture

```
Agent Runtime (Claude / GPT / Any Agent)
         |
         v
┌─────────────────────────────────┐
│    Universal App Bridge (UAB)   │
│                                 │
│  ┌───────────┐  ┌────────────┐  │
│  │ Framework  │  │  Control   │  │
│  │ Detector   │──│  Router    │  │
│  └───────────┘  └────────────┘  │
│         |              |        │
│  ┌──────┴──────────────┴──────┐ │
│  │     Framework Plugins      │ │
│  │ Electron  Qt  GTK  WPF     │ │
│  │ Flutter  Java  Office      │ │
│  └────────────────────────────┘ │
│         |                       │
│  ┌──────┴─────────────────────┐ │
│  │       Unified API          │ │
│  │ enumerate() query() act()  │ │
│  │ state() subscribe()       │ │
│  └────────────────────────────┘ │
│                                 │
│  ┌────────────────────────────┐ │
│  │   Production Hardening     │ │
│  │ Cache  Permissions  Health │ │
│  │ Retry  Chains  Audit Log   │ │
│  └────────────────────────────┘ │
└─────────────────────────────────┘
```

## License

MIT
