/**
 * UAB Environment Detection — Automatically adapts behavior for desktop vs server.
 *
 * Detects the runtime context and exposes a unified config so the rest of UAB
 * doesn't need to care about where it's running.
 *
 * Environments:
 *   - desktop:   Interactive Windows session (Session 1+), full UIA/CDP access
 *   - server:    Non-interactive (SSH, service, container), uses Session Bridge
 *   - container: Docker/WSL/Hyper-V, limited or no desktop access
 *
 * Usage:
 *   import { env, UABEnvironment } from './environment.js';
 *   if (env.mode === 'desktop') { ... }
 *   if (env.hasDesktop) { ... }
 */

import { execSync } from 'child_process';

// ─── Types ────────────────────────────────────────────────────

export type RuntimeMode = 'desktop' | 'server' | 'container';

export interface EnvironmentInfo {
  /** Current runtime mode */
  mode: RuntimeMode;
  /** Whether a desktop session is reachable (directly or via bridge) */
  hasDesktop: boolean;
  /** Windows session ID (0 = non-interactive, 1+ = desktop) */
  sessionId: number;
  /** Whether running inside a container (Docker, WSL, etc.) */
  isContainer: boolean;
  /** Whether Session 0→1 bridge is needed for desktop access */
  needsBridge: boolean;
  /** Platform (always 'win32' for now) */
  platform: NodeJS.Platform;
  /** OS architecture */
  arch: string;
  /** Node.js version */
  nodeVersion: string;
}

export interface EnvironmentDefaults {
  /** Whether to use persistent connections. Desktop: true, Server: false */
  persistent: boolean;
  /** Whether to enable the Chrome extension WebSocket bridge */
  extensionBridge: boolean;
  /** Rate limit (actions per minute per PID). Desktop: 100, Server: 60 */
  rateLimit: number;
  /** Cache TTL multiplier. Server gets longer TTLs to reduce cross-session calls */
  cacheTTLMultiplier: number;
}

// ─── Detection ────────────────────────────────────────────────

let _cached: EnvironmentInfo | null = null;

function getSessionId(): number {
  try {
    const output = execSync(
      'powershell -NoProfile -Command "(Get-Process -Id $PID).SessionId"',
      { encoding: 'utf-8', timeout: 5000 }
    );
    return parseInt(output.trim(), 10) || 0;
  } catch {
    return -1;
  }
}

function detectContainer(): boolean {
  // Check common container indicators
  if (process.platform !== 'win32') {
    try {
      const cgroup = execSync('cat /proc/1/cgroup 2>/dev/null || echo ""', {
        encoding: 'utf-8', timeout: 2000
      });
      if (cgroup.includes('docker') || cgroup.includes('containerd')) return true;
    } catch { /* not Linux or no cgroup */ }
  }

  // Check WSL
  if (process.env.WSL_DISTRO_NAME) return true;

  // Check Docker env vars
  if (process.env.DOCKER_CONTAINER || process.env.container) return true;

  return false;
}

function canReachDesktop(sessionId: number): boolean {
  if (sessionId > 0) return true; // Already in desktop session

  // Session 0: try to verify Task Scheduler can bridge to Session 1
  try {
    execSync(
      'schtasks /Query /TN "\\Microsoft\\Windows\\Defrag\\ScheduledDefrag" >nul 2>&1',
      { timeout: 3000 }
    );
    return true; // schtasks works, bridge is available
  } catch {
    return false;
  }
}

// ─── Public API ───────────────────────────────────────────────

/**
 * Detect the current runtime environment (cached after first call).
 */
export function detectEnvironment(): EnvironmentInfo {
  if (_cached) return _cached;

  const platform = process.platform;
  const isContainer = detectContainer();
  const sessionId = platform === 'win32' ? getSessionId() : -1;

  let mode: RuntimeMode;
  let hasDesktop: boolean;
  let needsBridge: boolean;

  if (isContainer) {
    mode = 'container';
    hasDesktop = false;
    needsBridge = false;
  } else if (sessionId === 0) {
    mode = 'server';
    hasDesktop = canReachDesktop(sessionId);
    needsBridge = hasDesktop; // If desktop reachable, it's via bridge
  } else {
    mode = 'desktop';
    hasDesktop = true;
    needsBridge = false;
  }

  _cached = {
    mode,
    hasDesktop,
    sessionId,
    isContainer,
    needsBridge,
    platform,
    arch: process.arch,
    nodeVersion: process.version,
  };

  return _cached;
}

/**
 * Get environment-aware defaults for UABConnector options.
 */
export function getDefaults(mode?: RuntimeMode): EnvironmentDefaults {
  const m = mode || detectEnvironment().mode;

  switch (m) {
    case 'desktop':
      return {
        persistent: true,
        extensionBridge: true,
        rateLimit: 100,
        cacheTTLMultiplier: 1,
      };

    case 'server':
      return {
        persistent: false,        // Stateless — no long-lived connections
        extensionBridge: false,    // No browser extension in server mode
        rateLimit: 60,             // Lower rate limit for shared environments
        cacheTTLMultiplier: 2,     // Longer cache to reduce bridge calls
      };

    case 'container':
      return {
        persistent: false,
        extensionBridge: false,
        rateLimit: 30,             // Minimal rate for containers
        cacheTTLMultiplier: 3,     // Aggressive caching
      };
  }
}

/**
 * Reset cached environment (for testing).
 */
export function resetEnvironment(): void {
  _cached = null;
}

/** Shorthand: the current environment info. */
export const env = new Proxy({} as EnvironmentInfo, {
  get(_target, prop: string) {
    return (detectEnvironment() as unknown as Record<string, unknown>)[prop];
  },
});
