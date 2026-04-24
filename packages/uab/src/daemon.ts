/**
 * UAB daemon - JSON-RPC compatibility bridge for Lancelot.
 *
 * Lancelot's Python provider still speaks JSON-RPC over port 7900.
 * The standalone UAB runtime has moved toward connector/server APIs,
 * so this daemon keeps the old transport contract while exposing the
 * newer connector-backed capabilities behind it.
 */

import http from 'node:http';
import { pathToFileURL } from 'node:url';

import { UABConnector, type ConnectionInfo } from './connector.js';
import { detectEnvironment } from './environment.js';
import { createLogger } from './logger.js';
import type { AppProfile } from './registry.js';
import { uab } from './service.js';
import type { SpatialElement } from './spatial.js';
import type {
  ActionParams,
  ActionResult,
  ActionType,
  AppState,
  AtomicChainDef,
  AtomicChainResult,
  DetectedApp,
  ElementSelector,
  FocusedElementInfo,
  PathSelector,
  UIElement,
} from './types.js';

const log = createLogger('uab-daemon');

const VERSION = '1.3.0';
const DEFAULT_HOST = '127.0.0.1';
const DEFAULT_PORT = 7900;
const SUPPORTED_FRAMEWORKS = [
  'electron', 'browser', 'qt5', 'qt6', 'gtk3', 'gtk4',
  'wpf', 'winui', 'dotnet', 'flutter',
  'java-swing', 'javafx', 'office',
];
const STANDALONE_FEATURES = [
  'scan',
  'apps',
  'find',
  'focused',
  'findByPath',
  'watchChanges',
  'atomicChain',
  'smartInvoke',
];

const args = process.argv.slice(2);

function argValue(argsList: string[], name: string): string | undefined {
  const idx = argsList.indexOf(name);
  if (idx < 0) return undefined;
  const value = argsList[idx + 1];
  return value && !value.startsWith('--') ? value : undefined;
}

export function resolveDaemonBindHost(
  argsList: string[] = process.argv.slice(2),
  env: NodeJS.ProcessEnv = process.env,
): string {
  const requested = argValue(argsList, '--host') ?? env.UAB_DAEMON_HOST ?? DEFAULT_HOST;
  const host = requested.trim();
  return host || DEFAULT_HOST;
}

const portIdx = args.indexOf('--port');
const PORT = portIdx >= 0 ? parseInt(args[portIdx + 1], 10) : DEFAULT_PORT;
const HOST = resolveDaemonBindHost(args);

export interface RpcRequest {
  jsonrpc: string;
  method: string;
  params?: Record<string, unknown>;
  id: number | string | null;
}

export interface LegacyServiceLike {
  running: boolean;
  start(): Promise<void>;
  stop(): Promise<void>;
  executeChain(chain: Record<string, unknown>): Promise<unknown>;
}

export interface ConnectorLike {
  running: boolean;
  start(): Promise<void>;
  stop(): Promise<void>;
  scan(electronOnly?: boolean): Promise<AppProfile[]>;
  apps(): AppProfile[];
  find(query: string): Promise<AppProfile[]>;
  inspectPid(pid: number): Promise<AppProfile | null>;
  connect(target: number | string): Promise<ConnectionInfo>;
  disconnect(pid: number): Promise<void>;
  disconnectAll(): Promise<void>;
  isConnected(pid: number): boolean;
  getConnections(): ConnectionInfo[];
  enumerate(pid: number): Promise<UIElement[]>;
  query(pid: number, selector: ElementSelector): Promise<UIElement[]>;
  act(pid: number, elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult>;
  state(pid: number): Promise<AppState>;
  keypress(pid: number, key: string): Promise<ActionResult>;
  hotkey(pid: number, keys: string | string[]): Promise<ActionResult>;
  window(pid: number, action: string, params?: { x?: number; y?: number; width?: number; height?: number }): Promise<ActionResult>;
  screenshot(pid: number, outputPath?: string): Promise<ActionResult>;
  cacheStats(): unknown;
  auditLog(limit?: number): unknown;
  healthSummary(): unknown;
  runHealthChecks(): Promise<void>;
  spatialMap(pid: number, options?: Record<string, unknown>): Promise<unknown>;
  textMap(pid: number, format?: 'detailed' | 'compact' | 'json'): Promise<string>;
  findByDescription(pid: number, description: string): Promise<SpatialElement[]>;
  focused(pid: number): Promise<FocusedElementInfo>;
  findByPath(pid: number, selector: PathSelector): Promise<Array<{ name: string; type: string; automationId: string; bounds: { x: number; y: number; w: number; h: number }; patterns: string }>>;
  watchChanges(pid: number, durationMs?: number, pollMs?: number): Promise<unknown[]>;
  atomicChain(chain: AtomicChainDef): Promise<AtomicChainResult>;
  smartInvoke(pid: number, name: string, options?: { parent?: string; type?: string; occurrence?: 'first' | 'last' | number }): Promise<unknown>;
}

export interface DaemonDependencies {
  connector: ConnectorLike;
  legacyService: LegacyServiceLike;
}

function rpcOk(id: number | string | null, result: unknown): string {
  return JSON.stringify({ jsonrpc: '2.0', result, id });
}

function rpcErr(id: number | string | null, code: number, message: string): string {
  return JSON.stringify({ jsonrpc: '2.0', error: { code, message }, id });
}

function profileToDetectedApp(profile: AppProfile): DetectedApp {
  return {
    pid: profile.pid ?? 0,
    name: profile.name,
    path: profile.path ?? profile.executable,
    framework: profile.framework,
    confidence: profile.confidence,
    windowTitle: profile.windowTitle,
    connectionInfo: profile.connectionInfo,
  };
}

function profilesToDetectedApps(profiles: AppProfile[]): DetectedApp[] {
  return profiles.map(profileToDetectedApp);
}

function connectionToLegacyResult(info: ConnectionInfo) {
  return {
    success: true,
    pid: info.pid,
    name: info.name,
    framework: info.framework,
    connectionMethod: info.method,
    elementCount: info.elementCount,
  };
}

async function ensureConnected(connector: ConnectorLike, pid: number): Promise<void> {
  if (!connector.isConnected(pid)) {
    await connector.connect(pid);
  }
}

async function dispatchCommon(
  method: string,
  params: Record<string, unknown>,
  deps: DaemonDependencies,
): Promise<unknown> {
  const { connector, legacyService } = deps;

  switch (method) {
    case 'ping':
      return { pong: true, timestamp: Date.now() };

    case 'version':
      return { version: VERSION, name: 'Universal App Bridge' };

    case 'environment':
      return detectEnvironment();

    case 'status':
    case 'getStatus': {
      const connections = connector.getConnections();
      return {
        version: VERSION,
        running: connector.running,
        connectedApps: connections.length,
        supportedFrameworks: SUPPORTED_FRAMEWORKS,
        standaloneFeatures: STANDALONE_FEATURES,
        transport: 'json-rpc-compat',
        connections,
      };
    }

    case 'scan': {
      const profiles = await connector.scan(Boolean(params.electronOnly));
      return {
        count: profiles.length,
        apps: profilesToDetectedApps(profiles),
      };
    }

    case 'apps':
      return connector.apps();

    case 'find': {
      const query = params.query as string;
      if (!query) {
        throw new Error('find requires "query" parameter');
      }
      const profiles = await connector.find(query);
      return {
        query,
        count: profiles.length,
        apps: profilesToDetectedApps(profiles),
      };
    }

    case 'detect':
    case 'detect.all':
      return profilesToDetectedApps(await connector.scan(false));

    case 'detect.electron':
      return profilesToDetectedApps(await connector.scan(true));

    case 'detect.byPid': {
      const profile = await connector.inspectPid(params.pid as number);
      return profile ? profileToDetectedApp(profile) : null;
    }

    case 'detect.byName': {
      const name = params.name as string;
      if (!name) {
        throw new Error('detect.byName requires "name" parameter');
      }
      return profilesToDetectedApps(await connector.find(name));
    }

    case 'connect': {
      let target: number | string | undefined;
      if (params.app) {
        const app = params.app as DetectedApp;
        target = app.pid || app.name;
      } else if (params.pid !== undefined) {
        target = params.pid as number;
      } else if (params.name) {
        target = params.name as string;
      }

      if (target === undefined || target === '') {
        throw new Error('connect requires "pid", "name", or "app" parameter');
      }

      return connectionToLegacyResult(await connector.connect(target));
    }

    case 'disconnect':
      await connector.disconnect(params.pid as number);
      return { disconnected: true };

    case 'disconnectAll':
      await connector.disconnectAll();
      return { disconnected: true };

    case 'connections':
      return connector.getConnections();

    case 'enumerate': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.enumerate(pid);
    }

    case 'query': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.query(pid, (params.selector ?? {}) as ElementSelector);
    }

    case 'act': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.act(
        pid,
        (params.elementId as string) || '',
        params.action as ActionType,
        (params.params ?? {}) as ActionParams,
      );
    }

    case 'state': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.state(pid);
    }

    case 'keypress': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.keypress(pid, params.key as string);
    }

    case 'hotkey': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.hotkey(pid, params.keys as string | string[]);
    }

    case 'minimize': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.window(pid, 'minimize');
    }

    case 'maximize': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.window(pid, 'maximize');
    }

    case 'restore': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.window(pid, 'restore');
    }

    case 'closeWindow': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.window(pid, 'close');
    }

    case 'moveWindow': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.window(pid, 'move', {
        x: params.x as number,
        y: params.y as number,
      });
    }

    case 'resizeWindow': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.window(pid, 'resize', {
        width: params.width as number,
        height: params.height as number,
      });
    }

    case 'screenshot': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.screenshot(pid, params.outputPath as string | undefined);
    }

    case 'chain':
      return legacyService.executeChain(params);

    case 'health':
      return connector.healthSummary();

    case 'cacheStats':
      return connector.cacheStats();

    case 'auditLog':
      return connector.auditLog((params.limit as number) || 50);

    case 'checkHealth':
      await connector.runHealthChecks();
      return { checked: true };

    case 'spatialMap': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.spatialMap(pid, params.options as Record<string, unknown> | undefined);
    }

    case 'textMap': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return {
        text: await connector.textMap(pid, (params.format as 'detailed' | 'compact' | 'json') || 'detailed'),
        timing: 0,
      };
    }

    case 'findByDescription': {
      const pid = params.pid as number;
      await ensureConnected(connector, pid);
      return connector.findByDescription(pid, params.description as string);
    }

    case 'focused':
      return connector.focused(params.pid as number);

    case 'findByPath': {
      const pid = params.pid as number;
      const selector: PathSelector = {
        path: params.path as string[] | undefined,
        name: params.name as string | undefined,
        parent: params.parent as string | undefined,
        type: params.type as PathSelector['type'],
        occurrence: params.occurrence as PathSelector['occurrence'],
      };
      const elements = await connector.findByPath(pid, selector);
      return { pid, count: elements.length, elements };
    }

    case 'watch':
    case 'watchChanges': {
      const pid = params.pid as number;
      const durationMs = (params.durationMs as number) || 3000;
      const pollMs = (params.pollMs as number) || 200;
      const events = await connector.watchChanges(pid, durationMs, pollMs);
      return { pid, eventCount: events.length, events };
    }

    case 'atomic':
    case 'atomicChain': {
      const pid = params.pid as number;
      const steps = params.steps as AtomicChainDef['steps'];
      if (!Array.isArray(steps) || steps.length === 0) {
        throw new Error('atomicChain requires "steps" array');
      }
      const label = (params.label as string) || 'atomic-chain';
      return {
        pid,
        label,
        ...(await connector.atomicChain({ pid, steps, label })),
      };
    }

    case 'smartInvoke': {
      const pid = params.pid as number;
      const name = params.name as string;
      if (!pid || !name) {
        throw new Error('smartInvoke requires "pid" and "name" parameters');
      }
      return {
        pid,
        name,
        ...(await connector.smartInvoke(pid, name, {
          parent: params.parent as string | undefined,
          type: params.type as string | undefined,
          occurrence: params.occurrence as 'first' | 'last' | number | undefined,
        }) as Record<string, unknown>),
      };
    }

    default:
      throw Object.assign(new Error(`Unknown method: ${method}`), { code: -32601 });
  }
}

export function createRpcDispatcher(deps: DaemonDependencies) {
  return (method: string, params: Record<string, unknown>) => dispatchCommon(method, params, deps);
}

const connector = new UABConnector({
  persistent: true,
  extensionBridge: process.env.UAB_ENABLE_EXTENSION_BRIDGE === '1',
});

const dispatch = createRpcDispatcher({
  connector,
  legacyService: uab as unknown as LegacyServiceLike,
});

const server = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status: 'ok',
      running: connector.running,
      connectorRunning: connector.running,
      legacyServiceRunning: uab.running,
      version: VERSION,
    }));
    return;
  }

  if (req.method !== 'POST') {
    res.writeHead(405, { 'Content-Type': 'application/json' });
    res.end(rpcErr(null, -32600, 'Only POST requests accepted'));
    return;
  }

  let body = '';
  req.on('data', (chunk: Buffer) => {
    body += chunk.toString();
  });

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
    } catch (err) {
      const code = typeof (err as { code?: unknown }).code === 'number'
        ? (err as { code: number }).code
        : -32000;
      const message = err instanceof Error ? err.message : 'Internal error';
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(rpcErr(id, code, message));
    }
  });
});

async function startup(): Promise<void> {
  log.info('starting_uab_daemon', { version: VERSION, host: HOST, port: PORT });
  log.info('starting_connector_runtime');
  await connector.start();
  try {
    log.info('starting_legacy_compatibility_service');
    await uab.start();
  } catch (err) {
    await connector.stop();
    throw err;
  }

  server.listen(PORT, HOST, () => {
    log.info('uab_daemon_listening', { url: `http://${HOST}:${PORT}` });
    log.info('accepting_json_rpc_requests');
  });
}

async function shutdown(signal: string): Promise<void> {
  log.info('shutting_down_uab_daemon', { signal });
  server.close();
  await Promise.allSettled([connector.stop(), uab.stop()]);
  log.info('uab_daemon_stopped');
  process.exit(0);
}

function isEntrypoint(): boolean {
  const entry = process.argv[1];
  return Boolean(entry) && import.meta.url === pathToFileURL(entry).href;
}

if (isEntrypoint()) {
  process.on('SIGINT', () => {
    void shutdown('SIGINT');
  });
  process.on('SIGTERM', () => {
    void shutdown('SIGTERM');
  });

  startup().catch((err) => {
    log.error('failed_to_start_uab_daemon', {
      error: err instanceof Error ? err.message : String(err),
    });
    process.exit(1);
  });
}
