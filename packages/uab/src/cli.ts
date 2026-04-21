#!/usr/bin/env node
/**
 * UAB CLI — Framework-independent command-line interface for the Universal App Bridge.
 *
 * Works with ANY AI agent framework:
 *   - Claude Code (Bash tool)
 *   - Codex CLI (shell commands)
 *   - Custom agents (subprocess)
 *   - MD-only agents (parse JSON output)
 *
 * New commands (connector layer):
 *   node dist/uab/cli.js scan              — Detect apps + save to registry
 *   node dist/uab/cli.js apps              — List known apps from registry (direct lookup, no scan)
 *   node dist/uab/cli.js find <name>       — Search registry, fallback to live detect
 *   node dist/uab/cli.js profiles          — Show registry file info
 *
 * Classic commands (all still work):
 *   node dist/uab/cli.js detect            — Scan for apps (alias for scan)
 *   node dist/uab/cli.js connect <name|pid>
 *   node dist/uab/cli.js enumerate <pid> [--depth N]
 *   node dist/uab/cli.js query <pid> [--type button] [--label "Submit"]
 *   node dist/uab/cli.js act <pid> <elementId> <action> [--text "hello"]
 *   node dist/uab/cli.js state <pid>
 *
 * All output is JSON for easy parsing by AI agents.
 * Profiles persist to data/uab-profiles/registry.json for cross-session knowledge.
 */

import { UABConnector } from './connector.js';
import type { ActionType, ElementType } from './types.js';
import {
  buildExcelWorkbookBenchmarkScript,
  probeExcelAutomation,
  resolveExcelWorkbookBenchmarkPaths,
  runExcelWorkbookBenchmark,
} from './plugins/office/index.js';

function parseArgs(argv: string[]): { command: string; args: string[]; flags: Record<string, string> } {
  const command = argv[0] || 'help';
  const args: string[] = [];
  const flags: Record<string, string> = {};

  for (let i = 1; i < argv.length; i++) {
    if (argv[i].startsWith('--')) {
      const key = argv[i].substring(2);
      const next = argv[i + 1];
      if (next && !next.startsWith('--')) {
        flags[key] = next;
        i++;
      } else {
        flags[key] = 'true';
      }
    } else {
      args.push(argv[i]);
    }
  }

  return { command, args, flags };
}

function output(data: unknown): void {
  console.log(JSON.stringify(data, null, 2));
}

function error(message: string): never {
  console.log(JSON.stringify({ error: message }));
  process.exit(1);
}

async function main() {
  const rawArgs = process.argv.slice(2);
  const { command, args, flags } = parseArgs(rawArgs);

  // Create connector (stateless mode — no health monitoring, no extension bridge)
  const connector = new UABConnector({
    profileDir: flags['profile-dir'] || 'data/uab-profiles',
    persistent: false,
    extensionBridge: false,
  });
  await connector.start();

  try {
    switch (command) {

      // ─── Discovery (new connector commands) ───────────────────
      case 'scan':
      case 'detect': {
        const electronOnly = flags.electron === 'true';
        const profiles = await connector.scan(electronOnly);
        output({
          count: profiles.length,
          apps: profiles.map(p => ({
            pid: p.pid,
            name: p.name,
            executable: p.executable,
            framework: p.framework,
            confidence: p.confidence,
            windowTitle: p.windowTitle,
          })),
          profilesSaved: true,
        });
        break;
      }

      case 'apps': {
        const profiles = connector.apps();
        if (profiles.length === 0) {
          output({
            count: 0,
            apps: [],
            hint: 'No apps in registry. Run "scan" first to detect and register apps.',
          });
        } else {
          const framework = flags.framework;
          const filtered = framework
            ? profiles.filter(p => p.framework === framework)
            : profiles;
          output({
            count: filtered.length,
            apps: filtered.map(p => ({
              pid: p.pid,
              name: p.name,
              executable: p.executable,
              framework: p.framework,
              confidence: p.confidence,
              preferredMethod: p.preferredMethod,
              lastSeen: new Date(p.lastSeen).toISOString(),
              tags: p.tags,
            })),
          });
        }
        break;
      }

      case 'find': {
        const query = args[0];
        if (!query) error('Usage: find <name>  (e.g., find notepad, find chrome)');
        const profiles = await connector.find(query);
        output({
          query,
          count: profiles.length,
          apps: profiles.map(p => ({
            pid: p.pid,
            name: p.name,
            executable: p.executable,
            framework: p.framework,
            confidence: p.confidence,
          })),
        });
        break;
      }

      case 'profiles': {
        const profiles = connector.apps();
        const { existsSync, statSync } = await import('fs');
        const profilePath = 'data/uab-profiles/registry.json';
        const exists = existsSync(profilePath);
        output({
          profilePath,
          exists,
          fileSize: exists ? statSync(profilePath).size : 0,
          appCount: profiles.length,
          frameworks: [...new Set(profiles.map(p => p.framework))],
          oldestEntry: profiles.length > 0
            ? new Date(Math.min(...profiles.map(p => p.lastSeen))).toISOString()
            : null,
          newestEntry: profiles.length > 0
            ? new Date(Math.max(...profiles.map(p => p.lastSeen))).toISOString()
            : null,
        });
        break;
      }

      // ─── Connection ───────────────────────────────────────────
      case 'connect': {
        const target = args[0];
        if (!target) error('Usage: connect <name|pid>');

        const pid = parseInt(target, 10);
        try {
          const info = !isNaN(pid)
            ? await connector.connect(pid)
            : await connector.connect(target);
          output({ connected: true, ...info });
        } catch (err) {
          // Check for multiple matches
          if (err instanceof Error && err.message.includes('Multiple')) {
            const profiles = await connector.find(target);
            output({
              error: 'multiple_matches',
              message: err.message,
              matches: profiles.map(p => ({ pid: p.pid, name: p.name, framework: p.framework })),
            });
            process.exit(1);
          }
          throw err;
        }
        await connector.disconnectAll();
        break;
      }

      // ─── UI Interaction ───────────────────────────────────────
      case 'enumerate': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: enumerate <pid> [--depth N]');
        const pid = parseInt(pidStr, 10);
        const maxDepth = parseInt(flags.depth || '3', 10);

        await connector.connect(pid);
        const elements = await connector.enumerate(pid, maxDepth);
        const flat = connector.flattenTree(elements, maxDepth);

        output({
          pid,
          totalElements: connector.countElements(elements),
          elements: flat,
        });
        await connector.disconnectAll();
        break;
      }

      case 'query': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: query <pid> [--type button] [--label "text"]');
        const pid = parseInt(pidStr, 10);

        await connector.connect(pid);
        const results = await connector.query(pid, {
          type: flags.type as ElementType | undefined,
          label: flags.label,
          limit: parseInt(flags.limit || '50', 10),
        });

        output({
          pid,
          count: results.length,
          elements: results.map(el => ({
            id: el.id,
            type: el.type,
            label: el.label,
            actions: el.actions,
            visible: el.visible,
            enabled: el.enabled,
          })),
        });
        await connector.disconnectAll();
        break;
      }

      case 'act': {
        const [pidStr, elementId, action] = args;
        if (!pidStr || !elementId || !action) error('Usage: act <pid> <elementId> <action> [--text "..."] [--value "..."]');
        const pid = parseInt(pidStr, 10);

        await connector.connect(pid);
        const result = await connector.act(pid, elementId, action as ActionType, {
          text: flags.text,
          value: flags.value,
          key: flags.key,
          keys: flags.keys ? flags.keys.split('+') : undefined,
          url: flags.url,
          script: flags.script,
          direction: flags.direction as 'up' | 'down' | 'left' | 'right' | undefined,
          amount: flags.amount ? parseInt(flags.amount, 10) : undefined,
          method: flags.method,
          x: flags.x ? parseInt(flags.x, 10) : undefined,
          y: flags.y ? parseInt(flags.y, 10) : undefined,
          width: flags.width ? parseInt(flags.width, 10) : undefined,
          height: flags.height ? parseInt(flags.height, 10) : undefined,
          outputPath: flags.output,
          row: flags.row ? parseInt(flags.row, 10) : undefined,
          col: flags.col ? parseInt(flags.col, 10) : undefined,
          cellRange: flags.range || flags.cellRange,
          sheet: flags.sheet,
          formula: flags.formula,
          to: flags.to,
          subject: flags.subject,
          body: flags.body,
          cc: flags.cc,
          folder: flags.folder,
          count: flags.count ? parseInt(flags.count, 10) : undefined,
          slideIndex: flags.slide ? parseInt(flags.slide, 10) : undefined,
        });

        output({ pid, elementId, action, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'state': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: state <pid>');
        const pid = parseInt(pidStr, 10);

        await connector.connect(pid);
        const state = await connector.state(pid);
        output({ pid, ...state });
        await connector.disconnectAll();
        break;
      }

      case 'excel-benchmark': {
        const rowCount = flags.rows ? parseInt(flags.rows, 10) : undefined;
        if (flags.rows && (rowCount === undefined || Number.isNaN(rowCount))) {
          error('Usage: excel-benchmark [--rows N] [--output file.xlsx] [--manifest file.json] [--visible] [--keep-open] [--dry-run]');
        }

        const options = {
          rowCount,
          outputPath: flags.output,
          manifestPath: flags.manifest,
          visible: flags.visible === 'true',
          keepOpen: flags['keep-open'] === 'true',
        };
        const paths = resolveExcelWorkbookBenchmarkPaths(options);

        if (flags['dry-run'] === 'true') {
          output({
            dryRun: true,
            options,
            resolvedPaths: paths,
            script: buildExcelWorkbookBenchmarkScript(options),
          });
          break;
        }

        output(runExcelWorkbookBenchmark(options));
        break;
      }

      case 'excel-probe': {
        output(probeExcelAutomation());
        break;
      }

      // ─── Keyboard Commands ────────────────────────────────────
      case 'keypress': {
        const [pidStr, key] = args;
        if (!pidStr || !key) error('Usage: keypress <pid> <key>  (e.g., keypress 1234 Enter)');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.keypress(pid, key);
        output({ pid, key, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'hotkey': {
        const pidStr = args[0];
        const combo = args.slice(1).join('+') || flags.keys || '';
        if (!pidStr || !combo) error('Usage: hotkey <pid> <key1+key2+...>  (e.g., hotkey 1234 ctrl+s)');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.hotkey(pid, combo);
        output({ pid, keys: combo.split('+'), ...result });
        await connector.disconnectAll();
        break;
      }

      // ─── Window Management ────────────────────────────────────
      case 'window': {
        const [pidStr, action] = args;
        if (!pidStr || !action) error('Usage: window <pid> <min|max|restore|close|move|resize> [--x N] [--y N] [--width N] [--height N]');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.window(pid, action, {
          x: flags.x ? parseInt(flags.x, 10) : undefined,
          y: flags.y ? parseInt(flags.y, 10) : undefined,
          width: flags.width ? parseInt(flags.width, 10) : undefined,
          height: flags.height ? parseInt(flags.height, 10) : undefined,
        });
        output({ pid, action, ...result });
        await connector.disconnectAll();
        break;
      }

      // ─── Screenshot ───────────────────────────────────────────
      case 'screenshot': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: screenshot <pid> [--output path.png]');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.screenshot(pid, flags.output);
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      // ─── Action Chain ─────────────────────────────────────────
      case 'chain': {
        const json = args[0] || flags.json;
        if (!json) error('Usage: chain <json>  or  chain --json \'{"name":"test","pid":1234,"steps":[...]}\'');
        let chain;
        try {
          chain = JSON.parse(json);
        } catch {
          error('Invalid JSON for chain definition');
        }

        // Build a full UAB service for chain execution (needs ChainExecutor)
        const { UABService } = await import('./service.js');
        const svc = new UABService();
        await svc.start();
        try {
          const result = await svc.executeChain(chain);
          output(result);
        } finally {
          await svc.stop();
        }
        break;
      }

      // ─── Spatial Map & Composite Query ──────────────────────
      case 'map': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: map <pid> [--format detailed|compact|json] [--depth N] [--no-text]');
        const pid = parseInt(pidStr, 10);
        const format = (flags.format || 'detailed') as 'detailed' | 'compact' | 'json';
        const maxDepth = parseInt(flags.depth || '12', 10);
        const readText = flags['no-text'] !== 'true';

        await connector.connect(pid);

        if (format === 'json') {
          const result = await connector.spatialMap(pid, { maxDepth, readText });
          const { renderJsonMap } = await import('./spatial.js');
          output({
            ...renderJsonMap(result.spatialMap) as object,
            textContent: Object.fromEntries(result.textContent),
            timing: result.timing,
          });
        } else {
          const text = await connector.textMap(pid, format);
          // For text formats, output as a single string (more readable)
          console.log(text);
        }
        await connector.disconnectAll();
        break;
      }

      case 'composite': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: composite <pid> [--depth N] [--no-text] [--vision]');
        const pid = parseInt(pidStr, 10);
        const maxDepth = parseInt(flags.depth || '12', 10);
        const readText = flags['no-text'] !== 'true';
        const includeVision = flags.vision === 'true';

        await connector.connect(pid);
        const result = await connector.spatialMap(pid, {
          maxDepth,
          readText,
          includeVision,
        });

        output({
          pid,
          totalElements: result.spatialMap.totalElements,
          rowCount: result.spatialMap.rows.length,
          window: result.spatialMap.windowBounds,
          textEnrichedCount: result.textEnrichedCount,
          timing: result.timing,
          rows: result.spatialMap.rows.map(row => ({
            index: row.index,
            y: row.y,
            height: row.height,
            elements: row.elements.map(el => ({
              id: el.id,
              type: el.type,
              label: el.label || undefined,
              text: el.text || result.textContent.get(el.id) || undefined,
              bounds: el.bounds,
              center: el.center,
              enabled: el.enabled,
              actions: el.actions,
              row: el.row,
              col: el.col,
            })),
          })),
        });
        await connector.disconnectAll();
        break;
      }

      case 'find-element': {
        const pidStr = args[0];
        const description = args.slice(1).join(' ') || flags.description;
        if (!pidStr || !description) error('Usage: find-element <pid> <description>  (e.g., find-element 1234 Submit button)');
        const pid = parseInt(pidStr, 10);

        await connector.connect(pid);
        const matches = await connector.findByDescription(pid, description);
        output({
          pid,
          query: description,
          count: matches.length,
          matches: matches.slice(0, 10).map(el => ({
            id: el.id,
            type: el.type,
            label: el.label,
            text: el.text,
            bounds: el.bounds,
            center: el.center,
            row: el.row,
            col: el.col,
          })),
        });
        await connector.disconnectAll();
        break;
      }

      case 'near': {
        const pidStr = args[0];
        const x = parseInt(args[1] || flags.x || '', 10);
        const y = parseInt(args[2] || flags.y || '', 10);
        const radius = parseInt(flags.radius || '100', 10);
        if (!pidStr || isNaN(x) || isNaN(y)) error('Usage: near <pid> <x> <y> [--radius N]');
        const pid = parseInt(pidStr, 10);

        await connector.connect(pid);
        const result = await connector.spatialMap(pid, { readText: false });
        const nearby = result.index.nearPoint(x, y, radius);
        output({
          pid,
          point: { x, y },
          radius,
          count: nearby.length,
          elements: nearby.slice(0, 20).map(n => ({
            id: n.element.id,
            type: n.element.type,
            label: n.element.label,
            distance: Math.round(n.distance),
            direction: n.direction,
            bounds: n.element.bounds,
          })),
        });
        await connector.disconnectAll();
        break;
      }

      // ─── Browser Session & Cookie Commands ────────────────────
      case 'cookies': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: cookies <pid> [--name "cookie_name"] [--domain ".example.com"] [--url "https://..."]');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.act(pid, '', 'getCookies' as ActionType, {
          cookieName: flags.name,
          domain: flags.domain,
          url: flags.url,
        });
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'setcookie': {
        const pidStr = args[0];
        if (!pidStr || !flags.name) error('Usage: setcookie <pid> --name "cookie_name" --value "val" [--domain ".example.com"] [--secure] [--httponly] [--samesite Lax] [--expires 1234567890]');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.act(pid, '', 'setCookie' as ActionType, {
          cookieName: flags.name,
          cookieValue: flags.value || '',
          domain: flags.domain,
          url: flags.url,
          secure: flags.secure === 'true',
          httpOnly: flags.httponly === 'true',
          sameSite: flags.samesite as 'Strict' | 'Lax' | 'None' | undefined,
          expires: flags.expires ? parseInt(flags.expires, 10) : undefined,
        });
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'deletecookie': {
        const pidStr = args[0];
        if (!pidStr || !flags.name) error('Usage: deletecookie <pid> --name "cookie_name" [--domain ".example.com"] [--url "https://..."]');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.act(pid, '', 'deleteCookie' as ActionType, {
          cookieName: flags.name,
          domain: flags.domain,
          url: flags.url,
        });
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'clearcookies': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: clearcookies <pid> [--domain ".example.com"]');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.act(pid, '', 'clearCookies' as ActionType, {
          domain: flags.domain,
        });
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'storage': {
        const pidStr = args[0];
        const storageType = (flags.type || 'local').toLowerCase();
        if (!pidStr) error('Usage: storage <pid> [--type local|session] [--key "key"] [--value "val"] [--action get|set|delete|clear]');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);

        const storageAction = (flags.action || 'get').toLowerCase();
        let actionType: ActionType;
        if (storageType === 'session') {
          actionType = (storageAction === 'set' ? 'setSessionStorage' :
                        storageAction === 'delete' ? 'deleteSessionStorage' :
                        storageAction === 'clear' ? 'clearSessionStorage' :
                        'getSessionStorage') as ActionType;
        } else {
          actionType = (storageAction === 'set' ? 'setLocalStorage' :
                        storageAction === 'delete' ? 'deleteLocalStorage' :
                        storageAction === 'clear' ? 'clearLocalStorage' :
                        'getLocalStorage') as ActionType;
        }

        const result = await connector.act(pid, '', actionType, {
          storageKey: flags.key,
          storageValue: flags.value,
        });
        output({ pid, storageType, action: storageAction, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'navigate': {
        const pidStr = args[0];
        const url = args[1] || flags.url;
        if (!pidStr || !url) error('Usage: navigate <pid> <url>');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.act(pid, '', 'navigate' as ActionType, { url });
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'tabs': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: tabs <pid>');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.act(pid, '', 'getTabs' as ActionType);
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'switchtab': {
        const [pidStr, tabId] = args;
        if (!pidStr || !tabId) error('Usage: switchtab <pid> <tabId|index>');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.act(pid, '', 'switchTab' as ActionType, { tabId });
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'newtab': {
        const pidStr = args[0];
        const url = args[1] || flags.url || 'about:blank';
        if (!pidStr) error('Usage: newtab <pid> [url]');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.act(pid, '', 'newTab' as ActionType, { url });
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'closetab': {
        const pidStr = args[0];
        const tabId = args[1] || flags.tab;
        if (!pidStr) error('Usage: closetab <pid> [tabId]');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.act(pid, '', 'closeTab' as ActionType, { tabId });
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'exec': {
        const pidStr = args[0];
        const script = args[1] || flags.script;
        if (!pidStr || !script) error('Usage: exec <pid> "<javascript>" OR exec <pid> --script "js code"');
        const pid = parseInt(pidStr, 10);
        await connector.connect(pid);
        const result = await connector.act(pid, '', 'executeScript' as ActionType, { script });
        output({ pid, ...result });
        await connector.disconnectAll();
        break;
      }

      case 'ext-status': {
        const { extensionExists, getExtensionVersion, getExtensionPath } = await import('./plugins/chrome-ext/installer.js');
        output({
          extensionPath: getExtensionPath(),
          extensionExists: extensionExists(),
          extensionVersion: extensionExists() ? getExtensionVersion() : null,
          note: 'Extension connection status is only available in service mode. CLI is stateless.',
          installInstructions: 'Load the extension via chrome://extensions > Developer mode > Load unpacked',
        });
        break;
      }

      case 'ext-install': {
        const { getInstallInstructions, extensionExists, generateIcons } = await import('./plugins/chrome-ext/installer.js');
        if (!extensionExists()) {
          error('Extension files not found in data/chrome-extension/');
        }
        generateIcons();
        output({
          success: true,
          instructions: getInstallInstructions(),
        });
        break;
      }

      // ─── Feature 1: Focus Tracking ─────────────────────────────
      case 'focused': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: focused <pid>  — Get the currently focused UI element via UIA FocusedElement');
        const pid = parseInt(pidStr, 10);
        const result = await connector.focused(pid);
        output(result);
        break;
      }

      // ─── Feature 2: Element Path Addressing ──────────────────────
      case 'find-path': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: find-path <pid> --path "Menu,File,Save" OR --name "Close" --parent "Dialog"');
        const pid = parseInt(pidStr, 10);
        const pathStr = flags.path;
        const nameStr = flags.name;
        const parentStr = flags.parent;
        const typeStr = flags.type;
        const occ = flags.occurrence || 'first';

        const selector: any = {
          occurrence: occ === 'first' || occ === 'last' ? occ : parseInt(occ, 10),
        };
        if (pathStr) selector.path = pathStr.split(',').map((s: string) => s.trim());
        if (nameStr) selector.name = nameStr;
        if (parentStr) selector.parent = parentStr;
        if (typeStr) selector.type = typeStr;

        const elements = await connector.findByPath(pid, selector);
        output({ pid, count: elements.length, elements });
        break;
      }

      // ─── Feature 3: State-change Listener ────────────────────────
      case 'watch': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: watch <pid> [--duration 3000] [--poll 200]  — Watch for UI state changes');
        const pid = parseInt(pidStr, 10);
        const durationMs = parseInt(flags.duration || '3000', 10);
        const pollMs = parseInt(flags.poll || '200', 10);
        const events = await connector.watchChanges(pid, durationMs, pollMs);
        output({ pid, eventCount: events.length, events });
        break;
      }

      // ─── Feature 4: Atomic Action Chains ─────────────────────────
      case 'atomic': {
        const json = args[0] || flags.json;
        if (!json) error('Usage: atomic \'{"pid":1234,"steps":[{"action":"hotkey","keys":["alt","m"]},{"action":"wait","ms":300},{"action":"keypress","key":"Down"}]}\'');
        let def;
        try {
          def = JSON.parse(json);
        } catch {
          error('Invalid JSON for atomic chain');
        }
        const result = await connector.atomicChain(def);
        output(result);
        break;
      }

      // ─── Feature 5: Smart Element Resolution ─────────────────────
      case 'smart-invoke': {
        const pidStr = args[0];
        const name = args.slice(1).join(' ') || flags.name;
        if (!pidStr || !name) error('Usage: smart-invoke <pid> <element name> [--parent "Dialog"] [--type Button] [--occurrence last]');
        const pid = parseInt(pidStr, 10);
        const result = await connector.smartInvoke(pid, name, {
          parent: flags.parent,
          type: flags.type,
          occurrence: flags.occurrence === 'first' || flags.occurrence === 'last'
            ? flags.occurrence
            : flags.occurrence ? parseInt(flags.occurrence, 10) : undefined,
        });
        output(result);
        break;
      }

      // ─── Server Mode ─────────────────────────────────────────
      case 'serve': {
        const { UABServer } = await import('./server.js');
        const port = parseInt(flags.port || '3100', 10);
        const host = flags.host || '127.0.0.1';
        const apiKey = flags['api-key'] || undefined;

        const server = new UABServer({ port, host, apiKey });
        await server.start();
        output({
          status: 'running',
          address: server.address,
          authenticated: !!apiKey,
          endpoints: 'GET /info for full list',
        });

        // Keep running until SIGINT
        await new Promise<void>((resolve) => {
          process.on('SIGINT', async () => {
            await server.stop();
            resolve();
          });
          process.on('SIGTERM', async () => {
            await server.stop();
            resolve();
          });
        });
        break;
      }

      // ─── Environment Info ──────────────────────────────────────
      case 'env': {
        const { detectEnvironment, getDefaults } = await import('./environment.js');
        const envInfo = detectEnvironment();
        const defaults = getDefaults(envInfo.mode);
        output({ environment: envInfo, defaults });
        break;
      }

      case 'help':
      default:
        output({
          name: 'Universal App Bridge CLI',
          version: '1.3.0',
          description: 'Framework-independent desktop app control for AI agents (desktop + server)',
          connectorCommands: {
            scan: 'Detect apps + save to registry (persists across invocations) [--electron]',
            apps: 'List known apps from registry (direct lookup, no scanning) [--framework electron]',
            find: 'Search registry by name, fallback to live detect: find <name>',
            profiles: 'Show registry file info and stats',
          },
          commands: {
            detect: 'Alias for scan (backward compatible)',
            connect: 'Test connection to an app: connect <name|pid>',
            enumerate: 'List UI elements: enumerate <pid> [--depth N]',
            query: 'Search UI elements: query <pid> [--type button] [--label "text"]',
            act: 'Perform action: act <pid> <elementId> <action> [--text "..."]',
            state: 'Get app state: state <pid>',
            'excel-benchmark': 'Build a reproducible Excel workbook benchmark: excel-benchmark [--rows 2000] [--output file.xlsx] [--manifest file.json] [--visible] [--keep-open] [--dry-run]',
            'excel-probe': 'Check whether local Excel COM automation is available: excel-probe',
            keypress: 'Send keypress: keypress <pid> <key>  (Enter, Tab, F5, a, etc.)',
            hotkey: 'Send hotkey: hotkey <pid> ctrl+s',
            window: 'Window control: window <pid> <min|max|restore|close|move|resize> [--x N --y N --width N --height N]',
            screenshot: 'Capture window: screenshot <pid> [--output path.png]',
            chain: 'Execute action chain: chain \'{"name":"test","pid":1234,"steps":[...]}\'',
            'ext-status': 'Check Chrome extension installation status',
            'ext-install': 'Generate icons and show install instructions',
          },
          browserCommands: {
            cookies: 'List cookies: cookies <pid> [--name "name"] [--domain ".example.com"]',
            setcookie: 'Set cookie: setcookie <pid> --name "name" --value "val" [--domain ".example.com"] [--secure] [--httponly] [--samesite Lax] [--expires timestamp]',
            deletecookie: 'Delete cookie: deletecookie <pid> --name "name" [--domain ".example.com"]',
            clearcookies: 'Clear cookies: clearcookies <pid> [--domain ".example.com"]',
            storage: 'Manage storage: storage <pid> [--type local|session] [--key "k"] [--value "v"] [--action get|set|delete|clear]',
            navigate: 'Navigate to URL: navigate <pid> <url>',
            tabs: 'List browser tabs: tabs <pid>',
            switchtab: 'Switch tab: switchtab <pid> <tabId|index>',
            newtab: 'Open new tab: newtab <pid> [url]',
            closetab: 'Close tab: closetab <pid> [tabId]',
            exec: 'Execute JavaScript: exec <pid> "document.title"',
          },
          officeActions: {
            readDocument: 'Read Word document content: act <pid> _ readDocument',
            readCell: 'Read Excel cell (UIA): act <pid> _ readCell --range A1:C5',
            writeCell: 'Write Excel cell (UIA): act <pid> _ writeCell --row 1 --col 1 --text "value"',
            readRange: 'Read Excel range (COM): act <pid> _ readRange --range A1:D10 [--sheet Sheet1]',
            writeRange: 'Write Excel range (COM): act <pid> _ writeRange --range A1 --text "value" [--formula "=SUM(A1:A5)"]',
            getSheets: 'List Excel sheets (COM): act <pid> _ getSheets',
            readFormula: 'Read Excel formulas (COM): act <pid> _ readFormula --range A1:A5',
            readSlides: 'Read PowerPoint slides (COM): act <pid> _ readSlides',
            readSlideText: 'Read slide text (COM): act <pid> _ readSlideText --slide 1',
            readEmails: 'Read Outlook emails (COM): act <pid> _ readEmails --folder Inbox --count 5',
            composeEmail: 'Create draft email (COM): act <pid> _ composeEmail --to addr --subject subj --body text',
            sendEmail: 'Send email (COM): act <pid> _ sendEmail --to addr --subject subj --body text',
          },
          spatialCommands: {
            map: 'Generate spatial map: map <pid> [--format detailed|compact|json] [--depth N] [--no-text]',
            composite: 'Full composite query (map + text + optional vision): composite <pid> [--depth N] [--no-text] [--vision]',
            'find-element': 'Find element by description: find-element <pid> <description>',
            near: 'Find elements near a point: near <pid> <x> <y> [--radius N]',
          },
          advancedCommands: {
            focused: 'Get focused element via UIA FocusedElement: focused <pid>',
            'find-path': 'Find by tree path: find-path <pid> --path "File,Save" OR --name "Close" --parent "Dialog"',
            watch: 'Watch for state changes: watch <pid> [--duration 3000] [--poll 200]',
            atomic: 'Atomic action chain (single PS session): atomic \'{"pid":N,"steps":[...]}\'',
            'smart-invoke': 'Smart invoke (auto-fallback): smart-invoke <pid> <name> [--parent "..."] [--type Button]',
          },
          serverCommands: {
            serve: 'Start HTTP server: serve [--port 3100] [--host 127.0.0.1] [--api-key secret]',
            env: 'Show detected environment and defaults (desktop/server/container)',
          },
          globalFlags: {
            '--profile-dir': 'Custom profile directory (default: data/uab-profiles)',
          },
        });
        break;
    }
  } catch (err) {
    error(err instanceof Error ? err.message : String(err));
  } finally {
    // Force exit after brief cleanup — prevents hanging on connection teardown
    const exitTimer = setTimeout(() => process.exit(0), 2000);
    exitTimer.unref();
    try { await connector.stop(); } catch { /* ignore cleanup errors */ }
    process.exit(0);
  }
}

main();
