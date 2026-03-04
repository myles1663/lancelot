/**
 * UAB Daemon — Thin HTTP JSON-RPC 2.0 wrapper around UABService.
 *
 * Lancelot (in Docker) communicates with UAB (on the Windows host)
 * over HTTP.  UAB v0.5.0 is a library, not a daemon, so this file
 * bridges that gap: it starts UABService, opens port 7900, and
 * dispatches JSON-RPC requests to the service methods.
 *
 * Usage:
 *   node dist/daemon.js                     # default port 7900
 *   node dist/daemon.js --port 8100         # custom port
 *
 * All requests: POST / with JSON-RPC 2.0 body.
 */

import http from 'node:http';
import { uab } from './service.js';
import type { DetectedApp, ActionType, ActionParams } from './types.js';

// ── CLI args ───────────────────────────────────────────────────────
const args = process.argv.slice(2);
const portIdx = args.indexOf('--port');
const PORT = portIdx >= 0 ? parseInt(args[portIdx + 1], 10) : 7900;
const HOST = '0.0.0.0';

// ── Supported frameworks (for getStatus) ───────────────────────────
const SUPPORTED_FRAMEWORKS = [
  'electron', 'qt5', 'qt6', 'gtk3', 'gtk4',
  'wpf', 'winui', 'dotnet', 'flutter',
  'java-swing', 'javafx', 'office',
];

// ── JSON-RPC helpers ───────────────────────────────────────────────
interface RpcRequest {
  jsonrpc: string;
  method: string;
  params?: Record<string, unknown>;
  id: number | string | null;
}

function rpcOk(id: number | string | null, result: unknown) {
  return JSON.stringify({ jsonrpc: '2.0', result, id });
}

function rpcErr(id: number | string | null, code: number, message: string) {
  return JSON.stringify({ jsonrpc: '2.0', error: { code, message }, id });
}

// ── Method dispatch ────────────────────────────────────────────────
async function dispatch(method: string, params: Record<string, unknown>): Promise<unknown> {
  switch (method) {

    // ── Meta ──────────────────────────────────────────────────────
    case 'ping':
      return { pong: true, timestamp: Date.now() };

    case 'version':
      return { version: '0.5.0', name: 'Universal App Bridge' };

    case 'status': {
      const connections = uab.getConnections();
      return {
        running: uab.running,
        frameworks: SUPPORTED_FRAMEWORKS,
        connections,
      };
    }

    case 'getStatus': {
      const connections = uab.getConnections();
      return {
        version: '0.5.0',
        running: uab.running,
        connectedApps: connections.length,
        supportedFrameworks: SUPPORTED_FRAMEWORKS,
        connections,
      };
    }

    // ── Discovery ────────────────────────────────────────────────
    case 'detect':
    case 'detect.all':
      return await uab.detect();

    case 'detect.electron':
      return await uab.detectElectron();

    case 'detect.byPid':
      return await uab.detectByPid(params.pid as number);

    case 'detect.byName':
      return await uab.findByName(params.name as string);

    // ── Connection ───────────────────────────────────────────────
    case 'connect': {
      // Backward-compatible: accept {pid}, {name}, or {app: DetectedApp}
      if (params.app) {
        const app = params.app as DetectedApp;
        const result = await uab.connect(app);
        return { success: true, pid: app.pid, name: app.name,
                 framework: app.framework, connectionMethod: result.method };
      }
      if (params.pid) {
        const app = await uab.detectByPid(params.pid as number);
        if (!app) throw new Error(`No app found at PID ${params.pid}`);
        const result = await uab.connect(app);
        return { success: true, pid: app.pid, name: app.name,
                 framework: app.framework, connectionMethod: result.method };
      }
      if (params.name) {
        const result = await uab.connectByName(params.name as string);
        return { success: true, pid: result.pid, name: result.app.name,
                 framework: result.app.framework, connectionMethod: result.method };
      }
      throw new Error('connect requires "pid", "name", or "app" parameter');
    }

    case 'disconnect':
      await uab.disconnect(params.pid as number);
      return { disconnected: true };

    case 'disconnectAll':
      await uab.disconnectAll();
      return { disconnected: true };

    case 'connections':
      return uab.getConnections();

    // ── Unified API ──────────────────────────────────────────────
    case 'enumerate':
      return await uab.enumerate(params.pid as number);

    case 'query':
      return await uab.query(
        params.pid as number,
        (params.selector ?? {}) as Record<string, unknown>,
      );

    case 'act':
      return await uab.act(
        params.pid as number,
        params.elementId as string,
        params.action as ActionType,
        (params.params ?? {}) as ActionParams,
      );

    case 'state':
      return await uab.state(params.pid as number);

    // ── Keyboard ─────────────────────────────────────────────────
    case 'keypress':
      return await uab.keypress(params.pid as number, params.key as string);

    case 'hotkey':
      return await uab.hotkey(params.pid as number, params.keys as string[]);

    // ── Window management ────────────────────────────────────────
    case 'minimize':
      return await uab.minimize(params.pid as number);

    case 'maximize':
      return await uab.maximize(params.pid as number);

    case 'restore':
      return await uab.restore(params.pid as number);

    case 'closeWindow':
      return await uab.closeWindow(params.pid as number);

    case 'moveWindow':
      return await uab.moveWindow(
        params.pid as number,
        params.x as number,
        params.y as number,
      );

    case 'resizeWindow':
      return await uab.resizeWindow(
        params.pid as number,
        params.width as number,
        params.height as number,
      );

    // ── Screenshot ───────────────────────────────────────────────
    case 'screenshot':
      return await uab.screenshot(
        params.pid as number,
        params.outputPath as string | undefined,
      );

    // ── Action Chains ────────────────────────────────────────────
    case 'chain':
      return await uab.executeChain(params as any);

    // ── Diagnostics ──────────────────────────────────────────────
    case 'health':
      return uab.getHealthSummary();

    case 'cacheStats':
      return uab.getCacheStats();

    case 'auditLog':
      return uab.getAuditLog((params.limit as number) || 50);

    case 'checkHealth':
      await uab.checkHealth();
      return { checked: true };

    default:
      throw Object.assign(
        new Error(`Unknown method: ${method}`),
        { code: -32601 },
      );
  }
}

// ── HTTP server ────────────────────────────────────────────────────
const server = http.createServer((req, res) => {
  // Health probe on GET /health
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', running: uab.running }));
    return;
  }

  // Only POST accepted for RPC
  if (req.method !== 'POST') {
    res.writeHead(405, { 'Content-Type': 'application/json' });
    res.end(rpcErr(null, -32600, 'Only POST requests accepted'));
    return;
  }

  let body = '';
  req.on('data', (chunk: Buffer) => { body += chunk.toString(); });

  req.on('end', async () => {
    let id: number | string | null = null;
    try {
      const rpc: RpcRequest = JSON.parse(body);
      id = rpc.id ?? null;

      if (!rpc.method || typeof rpc.method !== 'string') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(rpcErr(id, -32600, 'Missing or invalid "method" field'));
        return;
      }

      const result = await dispatch(rpc.method, (rpc.params ?? {}) as Record<string, unknown>);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(rpcOk(id, result));

    } catch (err: any) {
      const code = err.code && typeof err.code === 'number' ? err.code : -32000;
      const message = err.message || 'Internal error';
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(rpcErr(id, code, message));
    }
  });
});

// ── Lifecycle ──────────────────────────────────────────────────────
async function startup() {
  console.log('════════════════════════════════════════════════════');
  console.log('  Universal App Bridge (UAB) v0.5.0 — Daemon Mode');
  console.log('════════════════════════════════════════════════════');
  console.log();

  console.log('Starting UAB service...');
  await uab.start();
  console.log('UAB service started. Frameworks:', SUPPORTED_FRAMEWORKS.join(', '));

  server.listen(PORT, HOST, () => {
    console.log(`UAB daemon listening on http://${HOST}:${PORT}`);
    console.log('Accepting JSON-RPC 2.0 POST requests.');
    console.log();
  });
}

async function shutdown(signal: string) {
  console.log(`\n${signal} received — shutting down UAB daemon...`);
  server.close();
  await uab.stop();
  console.log('UAB daemon stopped.');
  process.exit(0);
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));

startup().catch((err) => {
  console.error('Failed to start UAB daemon:', err);
  process.exit(1);
});
