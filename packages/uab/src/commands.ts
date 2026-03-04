/**
 * UAB Telegram Commands
 *
 * Registers bot commands that let the user interact with
 * the Universal App Bridge directly from Telegram.
 *
 * Commands:
 *   /apps          — Scan for running desktop apps
 *   /appconnect    — Connect to an app by name or PID
 *   /appdisconnect — Disconnect from an app
 *   /ui            — Search UI elements in a connected app
 *   /click         — Click a UI element
 *   /apptype       — Type text into a UI element
 *   /appstate      — Get current app state
 *   /uabstatus     — Show UAB service status
 *   Phase 3:
 *   /keypress      — Send a keypress
 *   /hotkey        — Send a hotkey combo
 *   /appwin        — Window management
 *   /screenshot    — Capture window screenshot
 *   Phase 4:
 *   /uabhealth     — Connection health status
 *   /uabcache      — Cache statistics
 *   /uabaudit      — Recent action audit log
 *   /chain         — Execute action chain (JSON)
 */

import type { Bot, Context } from 'grammy';
import { uab } from './service.js';
import type { ElementType } from './types.js';
import type { ChainDefinition } from './chains.js';
import { createLogger } from './logger.js';

const log = createLogger('uab-commands');

export function registerUABCommands(bot: Bot<Context>): void {

  // ─── /apps — Scan for controllable apps ──────────────────────

  bot.command('apps', async (ctx) => {
    const status = await ctx.reply('🔍 Scanning for desktop apps...');

    try {
      const apps = await uab.detect();

      if (apps.length === 0) {
        await ctx.api.editMessageText(
          ctx.chat.id, status.message_id,
          '🖥️ <b>No controllable apps detected</b>\n\n' +
          '💡 <i>Tip: Electron apps need</i> <code>--remote-debugging-port=9222</code> <i>flag</i>',
          { parse_mode: 'HTML' },
        );
        return;
      }

      let text = `🖥️ <b>Detected ${apps.length} app(s):</b>\n\n`;
      for (const app of apps) {
        const conf = `${(app.confidence * 100).toFixed(0)}%`;
        const title = app.windowTitle ? ` — <i>${escapeHtml(app.windowTitle.substring(0, 40))}</i>` : '';
        const connected = uab.isConnected(app.pid) ? ' ✅' : '';
        text += `📱 <b>${escapeHtml(app.name)}</b> [${app.framework}]${connected}\n`;
        text += `   PID: <code>${app.pid}</code> | Confidence: ${conf}${title}\n\n`;
      }
      text += '💡 <i>Use /appconnect &lt;name|pid&gt; to connect</i>';

      await ctx.api.editMessageText(ctx.chat.id, status.message_id, text, { parse_mode: 'HTML' });
    } catch (err) {
      await ctx.api.editMessageText(
        ctx.chat.id, status.message_id,
        `❌ Scan failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  });

  // ─── /appconnect — Connect to an app ─────────────────────────

  bot.command('appconnect', async (ctx) => {
    const target = ctx.match?.trim();
    if (!target) {
      await ctx.reply('📱 Usage: <code>/appconnect &lt;app name or PID&gt;</code>\n\nExamples:\n• /appconnect Code\n• /appconnect 12345', { parse_mode: 'HTML' });
      return;
    }

    const status = await ctx.reply(`🔌 Connecting to <b>${escapeHtml(target)}</b>...`, { parse_mode: 'HTML' });

    try {
      // Try as PID first
      const pid = parseInt(target, 10);
      let result;

      if (!isNaN(pid)) {
        const app = await uab.detectByPid(pid);
        if (!app) {
          await ctx.api.editMessageText(ctx.chat.id, status.message_id, `❌ No detectable app at PID ${pid}`);
          return;
        }
        result = await uab.connect(app);
      } else {
        const { method, pid: connPid, app } = await uab.connectByName(target);
        result = { method, pid: connPid };
      }

      await ctx.api.editMessageText(
        ctx.chat.id, status.message_id,
        `✅ <b>Connected!</b>\n\n` +
        `📱 PID: <code>${result.pid}</code>\n` +
        `🔧 Method: ${result.method}\n\n` +
        `💡 <i>Use /ui to browse elements, /click to interact</i>`,
        { parse_mode: 'HTML' },
      );
    } catch (err) {
      await ctx.api.editMessageText(
        ctx.chat.id, status.message_id,
        `❌ Connection failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  });

  // ─── /appdisconnect — Disconnect from an app ─────────────────

  bot.command('appdisconnect', async (ctx) => {
    const pidStr = ctx.match?.trim();

    if (pidStr) {
      const pid = parseInt(pidStr, 10);
      if (isNaN(pid)) {
        await ctx.reply('Usage: <code>/appdisconnect [pid]</code>', { parse_mode: 'HTML' });
        return;
      }
      await uab.disconnect(pid);
      await ctx.reply(`✅ Disconnected from PID ${pid}`);
    } else {
      await uab.disconnectAll();
      await ctx.reply('✅ Disconnected from all apps');
    }
  });

  // ─── /ui — Browse/search UI elements ─────────────────────────

  bot.command('ui', async (ctx) => {
    const args = (ctx.match || '').trim().split(/\s+/);
    const connections = uab.getConnections();

    if (connections.length === 0) {
      await ctx.reply('❌ No apps connected. Use /appconnect first.');
      return;
    }

    // Parse args: /ui [pid] [type] [label...]
    let pid = connections[0].pid; // default to first connection
    let typeFilter: ElementType | undefined;
    let labelFilter: string | undefined;

    let argIdx = 0;
    // Check if first arg is a PID
    if (args[0] && !isNaN(parseInt(args[0], 10))) {
      pid = parseInt(args[0], 10);
      argIdx = 1;
    }
    // Check if next arg is a type
    const validTypes = ['button', 'textfield', 'textarea', 'link', 'checkbox', 'radio', 'select', 'menu', 'menuitem', 'tab', 'heading', 'image', 'list', 'listitem', 'dialog'];
    if (args[argIdx] && validTypes.includes(args[argIdx])) {
      typeFilter = args[argIdx] as ElementType;
      argIdx++;
    }
    // Remaining args are label filter
    if (args.slice(argIdx).join(' ').trim()) {
      labelFilter = args.slice(argIdx).join(' ').trim();
    }

    if (!uab.isConnected(pid)) {
      await ctx.reply(`❌ Not connected to PID ${pid}. Use /appconnect first.`);
      return;
    }

    const status = await ctx.reply('🔍 Searching UI elements...');

    try {
      const elements = await uab.query(pid, {
        type: typeFilter,
        label: labelFilter,
        limit: 20,
      });

      if (elements.length === 0) {
        const filterDesc = [typeFilter, labelFilter].filter(Boolean).join(' / ') || 'any';
        await ctx.api.editMessageText(
          ctx.chat.id, status.message_id,
          `🔍 No elements matching: ${filterDesc}`,
        );
        return;
      }

      let text = `🧩 <b>Found ${elements.length} element(s):</b>\n\n`;
      for (const el of elements.slice(0, 15)) {
        const label = el.label ? ` "${escapeHtml(el.label.substring(0, 50))}"` : '';
        const actions = el.actions.slice(0, 4).join(', ');
        text += `• <code>${el.id}</code> [${el.type}]${label}\n`;
        text += `  ⚡ ${actions}\n`;
      }
      if (elements.length > 15) {
        text += `\n<i>...and ${elements.length - 15} more</i>\n`;
      }
      text += '\n💡 <i>Use /click &lt;elementId&gt; to interact</i>';

      await ctx.api.editMessageText(ctx.chat.id, status.message_id, text, { parse_mode: 'HTML' });
    } catch (err) {
      await ctx.api.editMessageText(
        ctx.chat.id, status.message_id,
        `❌ Query failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  });

  // ─── /click — Click a UI element ──────────────────────────────

  bot.command('click', async (ctx) => {
    const elementId = ctx.match?.trim();
    if (!elementId) {
      await ctx.reply('Usage: <code>/click &lt;elementId&gt;</code>\n\n💡 Get element IDs with /ui', { parse_mode: 'HTML' });
      return;
    }

    const connections = uab.getConnections();
    if (connections.length === 0) {
      await ctx.reply('❌ No apps connected.');
      return;
    }

    // Try each connection until one owns this element
    for (const conn of connections) {
      try {
        const result = await uab.act(conn.pid, elementId, 'click');
        if (result.success) {
          await ctx.reply(`✅ Clicked <code>${escapeHtml(elementId)}</code>`, { parse_mode: 'HTML' });
        } else {
          await ctx.reply(`❌ Click failed: ${result.error}`);
        }
        return;
      } catch {
        continue;
      }
    }
    await ctx.reply(`❌ Element <code>${escapeHtml(elementId)}</code> not found in any connected app`, { parse_mode: 'HTML' });
  });

  // ─── /apptype — Type text into an element ─────────────────────

  bot.command('apptype', async (ctx) => {
    const args = (ctx.match || '').trim();
    const spaceIdx = args.indexOf(' ');
    if (spaceIdx === -1) {
      await ctx.reply('Usage: <code>/apptype &lt;elementId&gt; &lt;text&gt;</code>', { parse_mode: 'HTML' });
      return;
    }

    const elementId = args.substring(0, spaceIdx);
    const text = args.substring(spaceIdx + 1);

    const connections = uab.getConnections();
    if (connections.length === 0) {
      await ctx.reply('❌ No apps connected.');
      return;
    }

    for (const conn of connections) {
      try {
        const result = await uab.act(conn.pid, elementId, 'type', { text });
        if (result.success) {
          await ctx.reply(`✅ Typed into <code>${escapeHtml(elementId)}</code>`, { parse_mode: 'HTML' });
        } else {
          await ctx.reply(`❌ Type failed: ${result.error}`);
        }
        return;
      } catch {
        continue;
      }
    }
    await ctx.reply(`❌ Element not found in any connected app`);
  });

  // ─── /appstate — Get app state ────────────────────────────────

  bot.command('appstate', async (ctx) => {
    const pidStr = ctx.match?.trim();
    const connections = uab.getConnections();

    if (connections.length === 0) {
      await ctx.reply('❌ No apps connected.');
      return;
    }

    const pid = pidStr ? parseInt(pidStr, 10) : connections[0].pid;

    try {
      const state = await uab.state(pid);
      let text = '🪟 <b>App State:</b>\n\n';
      text += `📌 Title: <b>${escapeHtml(state.window.title)}</b>\n`;
      text += `📐 Size: ${state.window.size.width}×${state.window.size.height}\n`;
      text += `📍 Position: (${state.window.position.x}, ${state.window.position.y})\n`;
      text += `🎯 Focused: ${state.window.focused ? 'Yes' : 'No'}\n`;
      if (state.modals.length > 0) text += `🔲 Modals: ${state.modals.length} open\n`;

      await ctx.reply(text, { parse_mode: 'HTML' });
    } catch (err) {
      await ctx.reply(`❌ State query failed: ${err instanceof Error ? err.message : err}`);
    }
  });

  // ─── Phase 3: /keypress — Send a keypress ────────────────────

  bot.command('keypress', async (ctx) => {
    const key = ctx.match?.trim();
    if (!key) {
      await ctx.reply(
        '⌨️ Usage: <code>/keypress &lt;key&gt;</code>\n\n' +
        'Examples:\n• /keypress Enter\n• /keypress Tab\n• /keypress F5\n• /keypress a',
        { parse_mode: 'HTML' },
      );
      return;
    }

    const connections = uab.getConnections();
    if (connections.length === 0) {
      await ctx.reply('❌ No apps connected.');
      return;
    }

    const result = await uab.keypress(connections[0].pid, key);
    if (result.success) {
      await ctx.reply(`⌨️ Sent <code>${escapeHtml(key)}</code> to ${escapeHtml(connections[0].name)}`, { parse_mode: 'HTML' });
    } else {
      await ctx.reply(`❌ Keypress failed: ${result.error}`);
    }
  });

  // ─── Phase 3: /hotkey — Send a hotkey combo ─────────────────

  bot.command('hotkey', async (ctx) => {
    const args = ctx.match?.trim();
    if (!args) {
      await ctx.reply(
        '⌨️ Usage: <code>/hotkey &lt;key1+key2+...&gt;</code>\n\n' +
        'Examples:\n• /hotkey ctrl+s\n• /hotkey ctrl+shift+p\n• /hotkey alt+f4\n• /hotkey ctrl+c',
        { parse_mode: 'HTML' },
      );
      return;
    }

    const keys = args.split('+').map(k => k.trim());
    const connections = uab.getConnections();
    if (connections.length === 0) {
      await ctx.reply('❌ No apps connected.');
      return;
    }

    const result = await uab.hotkey(connections[0].pid, keys);
    if (result.success) {
      await ctx.reply(`⌨️ Sent <code>${escapeHtml(keys.join('+'))}</code> to ${escapeHtml(connections[0].name)}`, { parse_mode: 'HTML' });
    } else {
      await ctx.reply(`❌ Hotkey failed: ${result.error}`);
    }
  });

  // ─── Phase 3: /appwin — Window management ───────────────────

  bot.command('appwin', async (ctx) => {
    const args = (ctx.match || '').trim().split(/\s+/);
    const action = args[0]?.toLowerCase();

    if (!action || !['min', 'max', 'restore', 'close', 'move', 'resize'].includes(action)) {
      await ctx.reply(
        '🪟 Usage: <code>/appwin &lt;action&gt; [params]</code>\n\n' +
        'Actions:\n' +
        '• /appwin min — Minimize\n' +
        '• /appwin max — Maximize\n' +
        '• /appwin restore — Restore\n' +
        '• /appwin close — Close\n' +
        '• /appwin move 100 200 — Move to (x, y)\n' +
        '• /appwin resize 800 600 — Resize to w×h',
        { parse_mode: 'HTML' },
      );
      return;
    }

    const connections = uab.getConnections();
    if (connections.length === 0) {
      await ctx.reply('❌ No apps connected.');
      return;
    }
    const pid = connections[0].pid;

    let result;
    switch (action) {
      case 'min':
        result = await uab.minimize(pid);
        break;
      case 'max':
        result = await uab.maximize(pid);
        break;
      case 'restore':
        result = await uab.restore(pid);
        break;
      case 'close':
        result = await uab.closeWindow(pid);
        break;
      case 'move': {
        const x = parseInt(args[1], 10);
        const y = parseInt(args[2], 10);
        if (isNaN(x) || isNaN(y)) {
          await ctx.reply('Usage: <code>/appwin move &lt;x&gt; &lt;y&gt;</code>', { parse_mode: 'HTML' });
          return;
        }
        result = await uab.moveWindow(pid, x, y);
        break;
      }
      case 'resize': {
        const w = parseInt(args[1], 10);
        const h = parseInt(args[2], 10);
        if (isNaN(w) || isNaN(h)) {
          await ctx.reply('Usage: <code>/appwin resize &lt;width&gt; &lt;height&gt;</code>', { parse_mode: 'HTML' });
          return;
        }
        result = await uab.resizeWindow(pid, w, h);
        break;
      }
    }

    if (result?.success) {
      await ctx.reply(`🪟 ${action} ✅ on ${escapeHtml(connections[0].name)}`);
    } else {
      await ctx.reply(`❌ Window action failed: ${result?.error || 'unknown'}`);
    }
  });

  // ─── Phase 3: /screenshot — Capture window screenshot ───────

  bot.command('screenshot', async (ctx) => {
    const connections = uab.getConnections();
    if (connections.length === 0) {
      await ctx.reply('❌ No apps connected.');
      return;
    }

    const status = await ctx.reply('📸 Capturing screenshot...');
    const pid = connections[0].pid;

    try {
      const outPath = `data/screenshots/uab-${pid}-${Date.now()}.png`;
      const result = await uab.screenshot(pid, outPath);

      if (result.success) {
        const fs = await import('fs');
        const filePath = (result.result as string) || ((result as unknown as Record<string, unknown>).path as string) || outPath;

        if (fs.existsSync(filePath)) {
          const { InputFile } = await import('grammy');
          await ctx.replyWithPhoto(new InputFile(filePath), {
            caption: `📸 Screenshot of ${connections[0].name} (PID ${pid})`,
          });
          await ctx.api.deleteMessage(ctx.chat.id, status.message_id).catch(() => {});
        } else {
          await ctx.api.editMessageText(ctx.chat.id, status.message_id, `✅ Screenshot saved to ${filePath}`);
        }
      } else {
        await ctx.api.editMessageText(ctx.chat.id, status.message_id, `❌ Screenshot failed: ${result.error}`);
      }
    } catch (err) {
      await ctx.api.editMessageText(
        ctx.chat.id, status.message_id,
        `❌ Screenshot error: ${err instanceof Error ? err.message : err}`,
      );
    }
  });

  // ─── /uabstatus — Service status ──────────────────────────────

  bot.command('uabstatus', async (ctx) => {
    const connections = uab.getConnections();
    const cacheStats = uab.getCacheStats();

    let text = '🌉 <b>Universal App Bridge Status</b>\n\n';
    text += `⚡ Service: ${uab.running ? '🟢 Running' : '🔴 Stopped'}\n`;
    text += `🔌 Connections: ${connections.length}\n`;
    text += `📊 Cache hit rate: ${cacheStats.hitRate}%\n\n`;

    if (connections.length > 0) {
      text += '<b>Connected Apps:</b>\n';
      for (const conn of connections) {
        text += `  📱 ${escapeHtml(conn.name)} (PID ${conn.pid}) via ${conn.method}\n`;
      }
      text += '\n';
    }

    text += '<b>Available Commands:</b>\n';
    text += '  /apps — Scan for apps\n';
    text += '  /appconnect — Connect to app\n';
    text += '  /appdisconnect — Disconnect\n';
    text += '  /ui — Browse UI elements\n';
    text += '  /click — Click element\n';
    text += '  /apptype — Type into element\n';
    text += '  /appstate — App state info\n';
    text += '  ⌨️ /keypress — Send keypress\n';
    text += '  ⌨️ /hotkey — Send hotkey combo\n';
    text += '  🪟 /appwin — Window management\n';
    text += '  📸 /screenshot — Capture window\n';
    text += '  💚 /uabhealth — Connection health\n';
    text += '  📦 /uabcache — Cache stats\n';
    text += '  📋 /uabaudit — Action audit log\n';
    text += '  🔗 /chain — Execute action chain\n';

    await ctx.reply(text, { parse_mode: 'HTML' });
  });

  // ─── Phase 4: /uabhealth — Connection health ─────────────────

  bot.command('uabhealth', async (ctx) => {
    const health = uab.getHealthSummary();

    if (health.length === 0) {
      await ctx.reply('💚 <b>No active connections</b>\n\n💡 Use /appconnect to connect first.', { parse_mode: 'HTML' });
      return;
    }

    let text = '💚 <b>Connection Health</b>\n\n';
    for (const entry of health) {
      const icon = entry.healthy ? '🟢' : '🔴';
      const uptime = formatDuration(entry.uptimeMs);
      text += `${icon} <b>${escapeHtml(entry.name)}</b> (PID ${entry.pid})\n`;
      text += `   Method: ${entry.method} | Uptime: ${uptime}\n`;
      if (entry.failures > 0) {
        text += `   ⚠️ Health check failures: ${entry.failures}\n`;
      }
      text += '\n';
    }

    await ctx.reply(text, { parse_mode: 'HTML' });
  });

  // ─── Phase 4: /uabcache — Cache statistics ────────────────────

  bot.command('uabcache', async (ctx) => {
    const stats = uab.getCacheStats();

    let text = '📦 <b>Cache Statistics</b>\n\n';
    text += `📊 Hit rate: <b>${stats.hitRate}%</b>\n\n`;
    text += `🌳 Tree cache entries: ${stats.treeCacheSize}\n`;
    text += `🔍 Query cache entries: ${stats.queryCacheSize}\n`;
    text += `🪟 State cache entries: ${stats.stateCacheSize}\n\n`;
    text += `✅ Total hits: ${stats.totalHits}\n`;
    text += `❌ Total misses: ${stats.totalMisses}\n`;
    text += `🔄 Invalidations: ${stats.invalidations}\n`;

    await ctx.reply(text, { parse_mode: 'HTML' });
  });

  // ─── Phase 4: /uabaudit — Action audit log ────────────────────

  bot.command('uabaudit', async (ctx) => {
    const limit = parseInt(ctx.match?.trim() || '10', 10);
    const entries = uab.getAuditLog(limit);

    if (entries.length === 0) {
      await ctx.reply('📋 <b>No actions recorded yet</b>', { parse_mode: 'HTML' });
      return;
    }

    let text = `📋 <b>Recent Actions</b> (last ${entries.length}):\n\n`;
    for (const entry of entries.slice(-10)) {
      const time = new Date(entry.timestamp).toLocaleTimeString();
      const risk = entry.riskLevel === 'destructive' ? '🔴' : entry.riskLevel === 'moderate' ? '🟡' : '🟢';
      const status = entry.allowed ? '✅' : '🚫';
      text += `${risk} ${status} <code>${time}</code> ${entry.action} on ${escapeHtml(entry.appName)}\n`;
      if (entry.reason) text += `   💬 ${escapeHtml(entry.reason)}\n`;
    }

    await ctx.reply(text, { parse_mode: 'HTML' });
  });

  // ─── Phase 4: /chain — Execute an action chain ────────────────

  bot.command('chain', async (ctx) => {
    const json = ctx.match?.trim();
    if (!json) {
      await ctx.reply(
        '🔗 <b>Action Chains</b>\n\n' +
        'Execute multi-step workflows on connected apps.\n\n' +
        'Usage: <code>/chain {JSON chain definition}</code>\n\n' +
        'Example:\n<pre>' +
        escapeHtml(JSON.stringify({
          name: 'click-save',
          pid: 1234,
          steps: [
            { type: 'action', selector: { type: 'button', label: 'Save' }, action: 'click' },
            { type: 'wait', selector: { type: 'dialog' }, timeoutMs: 5000 },
          ],
        }, null, 2)) +
        '</pre>\n\n' +
        'Step types: action, wait, conditional, delay, keypress, hotkey, typeText',
        { parse_mode: 'HTML' },
      );
      return;
    }

    let chain: ChainDefinition;
    try {
      chain = JSON.parse(json);
    } catch {
      await ctx.reply('❌ Invalid JSON. Use /chain without args for help.');
      return;
    }

    const status = await ctx.reply(`🔗 Running chain "<b>${escapeHtml(chain.name || 'unnamed')}</b>"...`, { parse_mode: 'HTML' });

    try {
      const result = await uab.executeChain(chain);

      let text = result.success
        ? `✅ <b>Chain "${escapeHtml(chain.name)}" completed!</b>\n\n`
        : `❌ <b>Chain "${escapeHtml(chain.name)}" failed</b>\n\n`;

      text += `📊 Steps: ${result.stepsCompleted}/${result.totalSteps}\n`;
      text += `⏱️ Duration: ${result.durationMs}ms\n\n`;

      for (const step of result.steps.slice(0, 10)) {
        const icon = step.success ? '✅' : (step.skipped ? '⏭️' : '❌');
        const label = step.step.label || step.step.type;
        text += `${icon} Step ${step.stepIndex}: ${escapeHtml(label)} (${step.durationMs}ms)\n`;
        if (step.error) text += `   ❌ ${escapeHtml(step.error.substring(0, 100))}\n`;
      }

      if (result.error) {
        text += `\n⚠️ ${escapeHtml(result.error)}`;
      }

      await ctx.api.editMessageText(ctx.chat.id, status.message_id, text, { parse_mode: 'HTML' });
    } catch (err) {
      await ctx.api.editMessageText(
        ctx.chat.id, status.message_id,
        `❌ Chain error: ${err instanceof Error ? err.message : err}`,
      );
    }
  });
}

/** Format milliseconds as human-readable duration */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}m`;
  return `${(ms / 3_600_000).toFixed(1)}h`;
}

/** Escape HTML special characters for Telegram */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
