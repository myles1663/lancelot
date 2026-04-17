/**
 * Chrome Extension WebSocket Bridge Server
 *
 * Runs a local WebSocket server that the Chrome extension connects to.
 * Provides a request/response interface for sending commands to the
 * extension and receiving results.
 *
 * Protocol:
 *   Server → Extension: { id, method, params }
 *   Extension → Server: { id, result } or { id, error }
 *   Extension → Server: { type: "hello", version, browser }
 *   Server → Extension: { type: "welcome" }
 *   Server → Extension: { type: "ping" }
 *   Extension → Server: { type: "pong" } or { type: "heartbeat" }
 */

import { WebSocketServer, WebSocket } from 'ws';
import { createLogger } from '../../logger.js';

const log = createLogger('chrome-ext-ws');

export interface ExtensionInfo {
  version: string;
  browser: string;
  connectedAt: number;
}

interface PendingRequest {
  resolve: (result: unknown) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export class ExtensionWSServer {
  private wss: WebSocketServer | null = null;
  private client: WebSocket | null = null;
  private extensionInfo: ExtensionInfo | null = null;
  private pendingRequests: Map<number, PendingRequest> = new Map();
  private nextId = 1;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private readonly port: number;
  private readonly commandTimeout: number;

  constructor(port = 8787, commandTimeout = 30000) {
    this.port = port;
    this.commandTimeout = commandTimeout;
  }

  /** Start the WebSocket server */
  async start(): Promise<void> {
    if (this.wss) return;

    return new Promise((resolve, reject) => {
      this.wss = new WebSocketServer({ port: this.port, host: '127.0.0.1' });

      this.wss.on('listening', () => {
        log.info('Extension WS server listening', { port: this.port });
        this.startPingLoop();
        resolve();
      });

      this.wss.on('error', (err) => {
        log.error('WS server error', { error: err.message });
        reject(err);
      });

      this.wss.on('connection', (ws) => {
        this.handleConnection(ws);
      });
    });
  }

  /** Stop the WebSocket server */
  async stop(): Promise<void> {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }

    // Reject all pending requests
    for (const [id, pending] of this.pendingRequests) {
      pending.reject(new Error('Server shutting down'));
      clearTimeout(pending.timer);
    }
    this.pendingRequests.clear();

    if (this.client) {
      this.client.close();
      this.client = null;
    }

    if (this.wss) {
      this.wss.close();
      this.wss = null;
    }

    this.extensionInfo = null;
    log.info('Extension WS server stopped');
  }

  /** Check if an extension is connected */
  get connected(): boolean {
    return this.client !== null && this.client.readyState === WebSocket.OPEN;
  }

  /** Get info about the connected extension */
  get info(): ExtensionInfo | null {
    return this.extensionInfo;
  }

  /**
   * Send a command to the extension and wait for the result.
   *
   * @param method - Command method (e.g., "tabs.list", "dom.click")
   * @param params - Command parameters
   * @returns The result from the extension
   */
  async send(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    if (!this.connected) {
      throw new Error('No Chrome extension connected. Load the extension from data/chrome-extension/');
    }

    const id = this.nextId++;

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingRequests.delete(id);
        reject(new Error(`Command timed out after ${this.commandTimeout}ms: ${method}`));
      }, this.commandTimeout);

      this.pendingRequests.set(id, { resolve, reject, timer });

      this.client!.send(JSON.stringify({ id, method, params }));
    });
  }

  // ─── Private ────────────────────────────────────────────────

  private handleConnection(ws: WebSocket): void {
    // Only allow one extension connection at a time
    if (this.client && this.client.readyState === WebSocket.OPEN) {
      log.warn('Replacing existing extension connection');
      this.client.close();
    }

    this.client = ws;
    log.info('Extension connected');

    ws.on('message', (data) => {
      this.handleMessage(data.toString());
    });

    ws.on('close', () => {
      if (this.client === ws) {
        log.info('Extension disconnected');
        this.client = null;
        this.extensionInfo = null;

        // Reject all pending requests
        for (const [id, pending] of this.pendingRequests) {
          pending.reject(new Error('Extension disconnected'));
          clearTimeout(pending.timer);
        }
        this.pendingRequests.clear();
      }
    });

    ws.on('error', (err) => {
      log.error('Extension connection error', { error: err.message });
    });

    // Send welcome
    ws.send(JSON.stringify({ type: 'welcome', version: '1.0.0' }));
  }

  private handleMessage(raw: string): void {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(raw);
    } catch {
      log.warn('Invalid JSON from extension', { raw: raw.substring(0, 200) });
      return;
    }

    // Hello message from extension
    if (msg.type === 'hello') {
      this.extensionInfo = {
        version: String(msg.version || '?'),
        browser: String(msg.browser || 'unknown'),
        connectedAt: Date.now(),
      };
      log.info('Extension identified', { ...this.extensionInfo });
      return;
    }

    // Heartbeat / pong — just acknowledge
    if (msg.type === 'pong' || msg.type === 'heartbeat') {
      return;
    }

    // Command response
    if (typeof msg.id === 'number') {
      const pending = this.pendingRequests.get(msg.id);
      if (!pending) {
        log.warn('Response for unknown request', { id: msg.id });
        return;
      }

      this.pendingRequests.delete(msg.id);
      clearTimeout(pending.timer);

      if (msg.error) {
        pending.reject(new Error(String(msg.error)));
      } else {
        pending.resolve(msg.result);
      }
    }
  }

  private startPingLoop(): void {
    // Send ping every 20 seconds to keep extension's service worker alive
    this.pingTimer = setInterval(() => {
      if (this.connected) {
        this.client!.send(JSON.stringify({ type: 'ping' }));
      }
    }, 20000);
  }
}
