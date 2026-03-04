#!/usr/bin/env node
/**
 * UAB CLI — Standalone command-line interface for the Universal App Bridge.
 *
 * Designed for Claude to call via the Bash tool:
 *   node dist/uab/cli.js detect
 *   node dist/uab/cli.js detect --electron
 *   node dist/uab/cli.js connect <name|pid>
 *   node dist/uab/cli.js enumerate <pid> [--depth N]
 *   node dist/uab/cli.js query <pid> [--type button] [--label "Submit"]
 *   node dist/uab/cli.js act <pid> <elementId> <action> [--text "hello"] [--value "opt1"]
 *   node dist/uab/cli.js state <pid>
 *
 * All output is JSON for easy parsing by AI agents.
 * This CLI is stateless — each invocation creates fresh connections.
 * For persistent connections, use the UAB service via ClaudeClaw.
 */

import { FrameworkDetector } from './detector.js';
import { PluginManager } from './plugins/base.js';
import { ElectronPlugin } from './plugins/electron/index.js';
import { WinUIAPlugin } from './plugins/win-uia/index.js';
import { QtPlugin } from './plugins/qt/index.js';
import { GtkPlugin } from './plugins/gtk/index.js';
import { JavaPlugin } from './plugins/java/index.js';
import { FlutterPlugin } from './plugins/flutter/index.js';
import { OfficePlugin } from './plugins/office/index.js';
import { ControlRouter } from './router.js';
import type { ActionType, ElementType } from './types.js';
import { mkdirSync } from 'fs';
import { dirname } from 'path';

function createCore() {
  const detector = new FrameworkDetector();
  const pluginManager = new PluginManager();
  const router = new ControlRouter(pluginManager);
  pluginManager.register(new ElectronPlugin());
  pluginManager.register(new OfficePlugin());
  pluginManager.register(new QtPlugin());
  pluginManager.register(new GtkPlugin());
  pluginManager.register(new JavaPlugin());
  pluginManager.register(new FlutterPlugin());
  pluginManager.register(new WinUIAPlugin());
  return { detector, pluginManager, router };
}

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

function error(message: string): void {
  console.log(JSON.stringify({ error: message }));
  process.exit(1);
}

function countElements(elements: Array<{ children: Array<unknown> }>): number {
  let count = elements.length;
  for (const el of elements) {
    count += countElements(el.children as Array<{ children: Array<unknown> }>);
  }
  return count;
}

async function main() {
  const rawArgs = process.argv.slice(2);
  const { command, args, flags } = parseArgs(rawArgs);

  const { detector, pluginManager, router } = createCore();

  try {
    switch (command) {
      case 'detect': {
        const apps = flags.electron
          ? await detector.detectElectron()
          : await detector.detectAll();
        output({ count: apps.length, apps });
        break;
      }

      case 'connect': {
        const target = args[0];
        if (!target) error('Usage: connect <name|pid>');

        let app;
        const pid = parseInt(target, 10);
        if (!isNaN(pid)) {
          app = await detector.detectByPid(pid);
          if (!app) error(`No detectable app at PID ${pid}`);
        } else {
          const matches = await detector.findByName(target);
          if (matches.length === 0) error(`No app found matching "${target}"`);
          if (matches.length > 1) {
            output({
              error: 'multiple_matches',
              message: `Multiple apps match "${target}"`,
              matches: matches.map(m => ({ pid: m.pid, name: m.name, framework: m.framework })),
            });
            process.exit(1);
          }
          app = matches[0];
        }

        const conn = await router.connect(app!);
        const elements = await conn.enumerate();
        const count = countElements(elements as Array<{ children: Array<unknown> }>);

        output({
          connected: true,
          pid: app!.pid,
          name: app!.name,
          framework: app!.framework,
          method: (conn as { method?: string }).method || 'uab-hook',
          elementCount: count,
        });

        await router.disconnectAll();
        break;
      }

      case 'enumerate': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: enumerate <pid> [--depth N]');
        const pid = parseInt(pidStr, 10);
        const maxDepth = parseInt(flags.depth || '3', 10);

        const app = await detector.detectByPid(pid);
        if (!app) error(`No detectable app at PID ${pid}`);

        const conn = await router.connect(app!);
        const elements = await conn.enumerate();

        // Flatten to specified depth for JSON output
        function flattenTree(els: Array<{ id: string; type: string; label: string; actions: string[]; children: unknown[] }>, depth: number): unknown[] {
          const flat: unknown[] = [];
          for (const el of els) {
            flat.push({
              id: el.id,
              type: el.type,
              label: el.label,
              actions: el.actions,
              childCount: el.children.length,
              depth,
            });
            if (depth < maxDepth && el.children.length > 0) {
              flat.push(
                ...flattenTree(
                  el.children as Array<{ id: string; type: string; label: string; actions: string[]; children: unknown[] }>,
                  depth + 1,
                ),
              );
            }
          }
          return flat;
        }

        const flat = flattenTree(
          elements as Array<{ id: string; type: string; label: string; actions: string[]; children: unknown[] }>,
          0,
        );
        output({
          pid,
          totalElements: countElements(elements as Array<{ children: Array<unknown> }>),
          elements: flat,
        });

        await router.disconnectAll();
        break;
      }

      case 'query': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: query <pid> [--type button] [--label "text"]');
        const pid = parseInt(pidStr, 10);

        const app = await detector.detectByPid(pid);
        if (!app) error(`No detectable app at PID ${pid}`);

        const conn = await router.connect(app!);
        const results = await conn.query({
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

        await router.disconnectAll();
        break;
      }

      case 'act': {
        const [pidStr, elementId, action] = args;
        if (!pidStr || !elementId || !action) error('Usage: act <pid> <elementId> <action> [--text "..."] [--value "..."]');
        const pid = parseInt(pidStr, 10);

        const app = await detector.detectByPid(pid);
        if (!app) error(`No detectable app at PID ${pid}`);

        const conn = await router.connect(app!);
        const result = await conn.act(elementId, action as ActionType, {
          text: flags.text,
          value: flags.value,
          key: flags.key,
          keys: flags.keys ? flags.keys.split('+') : undefined,
          x: flags.x ? parseInt(flags.x, 10) : undefined,
          y: flags.y ? parseInt(flags.y, 10) : undefined,
          width: flags.width ? parseInt(flags.width, 10) : undefined,
          height: flags.height ? parseInt(flags.height, 10) : undefined,
          outputPath: flags.output,
          // Office-specific params
          row: flags.row ? parseInt(flags.row, 10) : undefined,
          col: flags.col ? parseInt(flags.col, 10) : undefined,
          cellRange: flags.range || flags.cellRange,
          sheet: flags.sheet,
          formula: flags.formula,
          // Outlook params
          to: flags.to,
          subject: flags.subject,
          body: flags.body,
          cc: flags.cc,
          folder: flags.folder,
          count: flags.count ? parseInt(flags.count, 10) : undefined,
          // PowerPoint params
          slideIndex: flags.slide ? parseInt(flags.slide, 10) : undefined,
        });

        output({ pid, elementId, action, ...result });

        await router.disconnectAll();
        break;
      }

      case 'state': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: state <pid>');
        const pid = parseInt(pidStr, 10);

        const app = await detector.detectByPid(pid);
        if (!app) error(`No detectable app at PID ${pid}`);

        const conn = await router.connect(app!);
        const state = await conn.state();

        output({ pid, ...state });

        await router.disconnectAll();
        break;
      }

      // ─── Phase 3: Keyboard Commands ──────────────────────────
      case 'keypress': {
        const [pidStr, key] = args;
        if (!pidStr || !key) error('Usage: keypress <pid> <key>  (e.g., keypress 1234 Enter)');
        const pid = parseInt(pidStr, 10);
        const app = await detector.detectByPid(pid);
        if (!app) error(`No detectable app at PID ${pid}`);
        const conn = await router.connect(app!);
        const result = await conn.act('', 'keypress' as ActionType, { key });
        output({ pid, key, ...result });
        await router.disconnectAll();
        break;
      }

      case 'hotkey': {
        const pidStr = args[0];
        const combo = args.slice(1).join('+') || flags.keys || '';
        if (!pidStr || !combo) error('Usage: hotkey <pid> <key1+key2+...>  (e.g., hotkey 1234 ctrl+s)');
        const pid = parseInt(pidStr, 10);
        const keys = combo.split('+').map((k: string) => k.trim());
        const app = await detector.detectByPid(pid);
        if (!app) error(`No detectable app at PID ${pid}`);
        const conn = await router.connect(app!);
        const result = await conn.act('', 'hotkey' as ActionType, { keys });
        output({ pid, keys, ...result });
        await router.disconnectAll();
        break;
      }

      // ─── Phase 3: Window Management ──────────────────────────
      case 'window': {
        const [pidStr, action] = args;
        if (!pidStr || !action) error('Usage: window <pid> <min|max|restore|close|move|resize> [--x N] [--y N] [--width N] [--height N]');
        const pid = parseInt(pidStr, 10);
        const app = await detector.detectByPid(pid);
        if (!app) error(`No detectable app at PID ${pid}`);
        const conn = await router.connect(app!);
        const actionMap: Record<string, ActionType> = {
          min: 'minimize', max: 'maximize', restore: 'restore', close: 'close',
          move: 'move', resize: 'resize',
          minimize: 'minimize', maximize: 'maximize',
        };
        const mappedAction = actionMap[action.toLowerCase()] || action;
        const result = await conn.act('', mappedAction as ActionType, {
          x: flags.x ? parseInt(flags.x, 10) : undefined,
          y: flags.y ? parseInt(flags.y, 10) : undefined,
          width: flags.width ? parseInt(flags.width, 10) : undefined,
          height: flags.height ? parseInt(flags.height, 10) : undefined,
        });
        output({ pid, action: mappedAction, ...result });
        await router.disconnectAll();
        break;
      }

      // ─── Phase 3: Screenshot ─────────────────────────────────
      case 'screenshot': {
        const pidStr = args[0];
        if (!pidStr) error('Usage: screenshot <pid> [--output path.png]');
        const pid = parseInt(pidStr, 10);
        const app = await detector.detectByPid(pid);
        if (!app) error(`No detectable app at PID ${pid}`);
        const conn = await router.connect(app!);
        const outPath = flags.output || `data/screenshots/uab-${pid}-${Date.now()}.png`;
        mkdirSync(dirname(outPath), { recursive: true });
        const result = await conn.act('', 'screenshot' as ActionType, { outputPath: outPath });
        output({ pid, ...result });
        await router.disconnectAll();
        break;
      }

      // ─── Phase 4: Action Chain ─────────────────────────────────
      case 'chain': {
        const json = args[0] || flags.json;
        if (!json) error('Usage: chain <json>  or  chain --json \'{"name":"test","pid":1234,"steps":[...]}\'');
        let chain;
        try {
          chain = JSON.parse(json);
        } catch {
          error('Invalid JSON for chain definition');
        }

        // Build a mini UAB service for chain execution
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

      case 'help':
      default:
        output({
          name: 'Universal App Bridge CLI',
          version: '0.4.0',
          commands: {
            detect: 'Scan for controllable desktop apps [--electron]',
            connect: 'Test connection to an app: connect <name|pid>',
            enumerate: 'List UI elements: enumerate <pid> [--depth N]',
            query: 'Search UI elements: query <pid> [--type button] [--label "text"]',
            act: 'Perform action: act <pid> <elementId> <action> [--text "..."]',
            state: 'Get app state: state <pid>',
            keypress: 'Send keypress: keypress <pid> <key>  (Enter, Tab, F5, a, etc.)',
            hotkey: 'Send hotkey: hotkey <pid> ctrl+s',
            window: 'Window control: window <pid> <min|max|restore|close|move|resize> [--x N --y N --width N --height N]',
            screenshot: 'Capture window: screenshot <pid> [--output path.png]',
            chain: 'Execute action chain: chain \'{"name":"test","pid":1234,"steps":[...]}\'',
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
        });
        break;
    }
  } catch (err) {
    error(err instanceof Error ? err.message : String(err));
  }
}

main();
