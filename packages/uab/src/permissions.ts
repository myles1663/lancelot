/**
 * UAB Permissions - daemon-local action safety model.
 *
 * Keep this taxonomy aligned with src/tools/providers/uab_bridge.py. The Python
 * bridge is the governance-facing classifier; this layer provides the daemon's
 * local confirmation and audit guardrails.
 */

import type { ActionType, DetectedApp } from './types.js';
import { readFileSync } from 'fs';
import { createLogger } from './logger.js';

const log = createLogger('uab-perms');

interface ActionRiskManifest {
  read_only: string[];
  mutating: string[];
  destructive: string[];
  sensitive_app_patterns: string[];
}

function loadActionRiskManifest(): ActionRiskManifest {
  const manifestUrl = new URL('../data/action-risk.json', import.meta.url);
  const manifest = JSON.parse(readFileSync(manifestUrl, 'utf-8')) as Partial<ActionRiskManifest>;

  for (const key of ['read_only', 'mutating', 'destructive', 'sensitive_app_patterns'] as const) {
    if (!Array.isArray(manifest[key])) {
      throw new Error(`UAB action risk manifest missing array: ${key}`);
    }
  }

  return manifest as ActionRiskManifest;
}

const ACTION_RISK_MANIFEST = loadActionRiskManifest();
const DESTRUCTIVE_ACTIONS = new Set(ACTION_RISK_MANIFEST.destructive);
const MODIFYING_ACTIONS = new Set(ACTION_RISK_MANIFEST.mutating);
const SAFE_ACTIONS = new Set(ACTION_RISK_MANIFEST.read_only);

export type RiskLevel = 'safe' | 'moderate' | 'destructive';

export interface PermissionCheck {
  allowed: boolean;
  riskLevel: RiskLevel;
  reason?: string;
}

export interface RateLimitEntry {
  count: number;
  windowStart: number;
}

export interface AuditEntry {
  timestamp: number;
  pid: number;
  appName: string;
  action: ActionType;
  elementId: string;
  riskLevel: RiskLevel;
  allowed: boolean;
  reason?: string;
}

export interface PermissionOptions {
  /** Whether to block destructive actions (default: false — just log them) */
  blockDestructive?: boolean;
  /** Rate limit: max actions per PID per window (default: 100) */
  rateLimit?: number;
  /** Rate limit window in ms (default: 60000 = 1 minute) */
  rateLimitWindow?: number;
  /** Max audit log entries to keep in memory (default: 1000) */
  maxAuditEntries?: number;
  /** PIDs that are exempt from rate limiting */
  exemptPids?: Set<number>;
}

export class PermissionManager {
  private options: Required<PermissionOptions>;
  private rateLimits: Map<number, RateLimitEntry> = new Map();
  private auditLog: AuditEntry[] = [];
  private allowedPids: Set<number> = new Set(); // PIDs confirmed for destructive actions

  constructor(options?: PermissionOptions) {
    this.options = {
      blockDestructive: options?.blockDestructive ?? false,
      rateLimit: options?.rateLimit ?? 100,
      rateLimitWindow: options?.rateLimitWindow ?? 60_000,
      maxAuditEntries: options?.maxAuditEntries ?? 1000,
      exemptPids: options?.exemptPids ?? new Set(),
    };
  }

  /** Check if an action is permitted */
  check(pid: number, action: ActionType, app?: DetectedApp): PermissionCheck {
    const riskLevel = this.getRiskLevel(action);

    // Rate limit check
    if (!this.options.exemptPids.has(pid)) {
      if (this.isRateLimited(pid)) {
        return {
          allowed: false,
          riskLevel,
          reason: `Rate limited: too many actions on PID ${pid} (max ${this.options.rateLimit}/min)`,
        };
      }
    }

    // Destructive action check
    if (riskLevel === 'destructive' && this.options.blockDestructive) {
      if (!this.allowedPids.has(pid)) {
        return {
          allowed: false,
          riskLevel,
          reason: `Destructive action "${action}" requires confirmation for PID ${pid}` +
            (app ? ` (${app.name})` : ''),
        };
      }
    }

    return { allowed: true, riskLevel };
  }

  /** Record an action in the rate limiter and audit log */
  record(
    pid: number,
    action: ActionType,
    elementId: string,
    app: DetectedApp,
    allowed: boolean,
    reason?: string,
  ): void {
    // Update rate limiter
    this.incrementRateLimit(pid);

    // Audit log
    const entry: AuditEntry = {
      timestamp: Date.now(),
      pid,
      appName: app.name,
      action,
      elementId,
      riskLevel: this.getRiskLevel(action),
      allowed,
      reason,
    };

    this.auditLog.push(entry);

    // Trim audit log if over limit
    if (this.auditLog.length > this.options.maxAuditEntries) {
      this.auditLog = this.auditLog.slice(-Math.floor(this.options.maxAuditEntries * 0.8));
    }

    if (entry.riskLevel !== 'safe') {
      log.info('Action recorded', {
        pid,
        app: app.name,
        action,
        risk: entry.riskLevel,
        allowed,
      });
    }
  }

  /** Confirm a PID for destructive actions (after user approval) */
  confirmDestructive(pid: number): void {
    this.allowedPids.add(pid);
    log.info('Destructive actions confirmed', { pid });
  }

  /** Revoke destructive action permission for a PID */
  revokeDestructive(pid: number): void {
    this.allowedPids.delete(pid);
  }

  /** Get the risk level of an action */
  getRiskLevel(action: ActionType): RiskLevel {
    if (DESTRUCTIVE_ACTIONS.has(action)) return 'destructive';
    if (MODIFYING_ACTIONS.has(action)) return 'moderate';
    if (SAFE_ACTIONS.has(action)) return 'safe';

    log.warn('Unknown UAB action risk classification; defaulting to moderate', { action });
    return 'moderate';
  }

  /** Get recent audit log entries */
  getAuditLog(limit = 50): AuditEntry[] {
    return this.auditLog.slice(-limit);
  }

  /** Get audit log for a specific PID */
  getAuditForPid(pid: number, limit = 50): AuditEntry[] {
    return this.auditLog
      .filter(e => e.pid === pid)
      .slice(-limit);
  }

  /** Get rate limit status for a PID */
  getRateLimitStatus(pid: number): { count: number; remaining: number; resetMs: number } {
    const entry = this.rateLimits.get(pid);
    const now = Date.now();

    if (!entry || now - entry.windowStart > this.options.rateLimitWindow) {
      return {
        count: 0,
        remaining: this.options.rateLimit,
        resetMs: 0,
      };
    }

    return {
      count: entry.count,
      remaining: Math.max(0, this.options.rateLimit - entry.count),
      resetMs: this.options.rateLimitWindow - (now - entry.windowStart),
    };
  }

  /** Clear rate limits and audit log */
  clear(): void {
    this.rateLimits.clear();
    this.auditLog = [];
    this.allowedPids.clear();
  }

  // ─── Internal ────────────────────────────────────────────────

  private isRateLimited(pid: number): boolean {
    const entry = this.rateLimits.get(pid);
    if (!entry) return false;

    const now = Date.now();
    if (now - entry.windowStart > this.options.rateLimitWindow) {
      // Window expired, reset
      this.rateLimits.delete(pid);
      return false;
    }

    return entry.count >= this.options.rateLimit;
  }

  private incrementRateLimit(pid: number): void {
    const now = Date.now();
    const entry = this.rateLimits.get(pid);

    if (!entry || now - entry.windowStart > this.options.rateLimitWindow) {
      this.rateLimits.set(pid, { count: 1, windowStart: now });
    } else {
      entry.count++;
    }
  }
}
