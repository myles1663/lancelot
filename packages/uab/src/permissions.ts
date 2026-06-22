/**
 * UAB Permissions - daemon-local action safety model.
 *
 * Keep this taxonomy aligned with docs/risk-terminology.md and the Python
 * bridge. The Python bridge is the governance-facing classifier; this layer
 * provides the daemon's local confirmation and audit guardrails.
 */

import type { ActionType, ActionParams, DetectedApp } from './types.js';
import { readFileSync } from 'fs';
import { createLogger } from './logger.js';
import {
  validateUABAuthorityGrant,
  type UABGrantValidationContext,
} from './governance/grants.js';

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
export const RISK_LEVELS: readonly RiskLevel[] = ['safe', 'moderate', 'destructive'] as const;
const ACTION_RISK_CATEGORY_TO_LEVEL = {
  read_only: 'safe',
  mutating: 'moderate',
  destructive: 'destructive',
} as const satisfies Record<keyof Pick<ActionRiskManifest, 'read_only' | 'mutating' | 'destructive'>, RiskLevel>;

export const RISK_TERMINOLOGY: Record<RiskLevel, {
  toolFabric: 'low' | 'medium' | 'high';
  governance: 'T0/T1' | 'T1/T2' | 'T3';
}> = {
  safe: { toolFabric: 'low', governance: 'T0/T1' },
  moderate: { toolFabric: 'medium', governance: 'T1/T2' },
  destructive: { toolFabric: 'high', governance: 'T3' },
};

function assertRiskLevel(value: string): asserts value is RiskLevel {
  if (!RISK_LEVELS.includes(value as RiskLevel)) {
    throw new Error(`Unknown UAB risk label: ${value}`);
  }
}

export function validateRiskTerminology(
  terminology: Record<string, { toolFabric: string; governance: string }> = RISK_TERMINOLOGY,
  manifest: Record<string, unknown> = ACTION_RISK_MANIFEST as unknown as Record<string, unknown>,
): void {
  const expectedTerminology = {
    safe: { toolFabric: 'low', governance: 'T0/T1' },
    moderate: { toolFabric: 'medium', governance: 'T1/T2' },
    destructive: { toolFabric: 'high', governance: 'T3' },
  } as const;

  const labels = Object.keys(terminology).sort();
  const expectedLabels = [...RISK_LEVELS].sort();
  if (labels.join('|') !== expectedLabels.join('|')) {
    throw new Error(`UAB risk terminology labels drifted: ${labels.join(',')}`);
  }

  for (const label of RISK_LEVELS) {
    const entry = terminology[label];
    const expected = expectedTerminology[label];
    if (!entry || entry.toolFabric !== expected.toolFabric || entry.governance !== expected.governance) {
      throw new Error(`UAB risk terminology mapping drifted for ${label}`);
    }
  }

  const requiredManifestKeys = new Set([
    ...Object.keys(ACTION_RISK_CATEGORY_TO_LEVEL),
    'sensitive_app_patterns',
  ]);
  const actualManifestKeys = Object.keys(manifest);
  const missing = [...requiredManifestKeys].filter(key => !(key in manifest));
  const extra = actualManifestKeys.filter(key => !requiredManifestKeys.has(key));
  if (missing.length > 0) {
    throw new Error(`UAB action risk manifest missing keys: ${missing.join(',')}`);
  }
  if (extra.length > 0) {
    throw new Error(`UAB action risk manifest has unknown keys: ${extra.join(',')}`);
  }

  const seen = new Map<string, string>();
  for (const [category, riskLevel] of Object.entries(ACTION_RISK_CATEGORY_TO_LEVEL)) {
    assertRiskLevel(riskLevel);
    const actions = manifest[category];
    if (!Array.isArray(actions) || actions.some(action => typeof action !== 'string')) {
      throw new Error(`UAB action risk manifest category must be a string array: ${category}`);
    }
    for (const action of actions as string[]) {
      const prior = seen.get(action);
      if (prior) {
        throw new Error(`UAB action ${action} appears in both ${prior} and ${category}`);
      }
      seen.set(action, category);
    }
  }

  const sensitivePatterns = manifest.sensitive_app_patterns;
  if (!Array.isArray(sensitivePatterns) || sensitivePatterns.some(pattern => typeof pattern !== 'string')) {
    throw new Error('UAB action risk manifest sensitive_app_patterns must be a string array');
  }
}

export interface PermissionCheck {
  allowed: boolean;
  riskLevel: RiskLevel;
  reasonCode?: string;
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
  /** HMAC key used to verify supplied central-authority UAB grants. */
  authoritySecret?: string | Buffer;
}

export class PermissionManager {
  private options: Required<Omit<PermissionOptions, 'authoritySecret'>> & {
    authoritySecret?: string | Buffer;
  };
  private rateLimits: Map<number, RateLimitEntry> = new Map();
  private auditLog: AuditEntry[] = [];
  private allowedPids: Set<number> = new Set(); // PIDs confirmed for destructive actions
  private usedAuthorityGrantNonces: Set<string> = new Set();

  constructor(options?: PermissionOptions) {
    this.options = {
      blockDestructive: options?.blockDestructive ?? false,
      rateLimit: options?.rateLimit ?? 100,
      rateLimitWindow: options?.rateLimitWindow ?? 60_000,
      maxAuditEntries: options?.maxAuditEntries ?? 1000,
      exemptPids: options?.exemptPids ?? new Set(),
      authoritySecret: options?.authoritySecret ?? process.env.UAB_AUTHORITY_GRANT_SECRET,
    };
  }

  /** Check if an action is permitted */
  check(
    pid: number,
    action: ActionType,
    app?: DetectedApp,
    params?: ActionParams,
    selectorScope?: string,
  ): PermissionCheck {
    const riskLevel = this.getRiskLevel(action);
    const grantRequired = this.requiresAuthorityGrant(action);

    if (params?.uabAuthorityGrant) {
      const grantContext: UABGrantValidationContext = {
        appName: app?.name,
        appPid: pid,
        action,
        selectorScope: selectorScope ?? params.selectorScope,
        expectedFlags: this.expectedGrantFlags(action, riskLevel),
      };
      const grantValidation = validateUABAuthorityGrant(
        params.uabAuthorityGrant,
        this.options.authoritySecret,
        grantContext,
      );
      if (!grantValidation.valid) {
        return {
          allowed: false,
          riskLevel,
          reasonCode: grantValidation.reasonCode,
          reason: `UAB authority grant rejected: ${grantValidation.reasonCode}`,
        };
      }
      const nonce = params.uabAuthorityGrant.nonce;
      if (this.usedAuthorityGrantNonces.has(nonce)) {
        return {
          allowed: false,
          riskLevel,
          reasonCode: 'replayed_nonce',
          reason: 'UAB authority grant rejected: replayed_nonce',
        };
      }
      this.usedAuthorityGrantNonces.add(nonce);
    } else if (grantRequired) {
      return {
        allowed: false,
        riskLevel,
        reasonCode: 'missing_authority_grant',
        reason: `UAB authority grant required for governed action "${action}"`,
      };
    }

    // Rate limit check
    if (!this.options.exemptPids.has(pid)) {
      if (this.isRateLimited(pid)) {
        return {
          allowed: false,
          riskLevel,
          reasonCode: 'rate_limited',
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
          reasonCode: 'destructive_confirmation_required',
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

    log.warn('Unknown UAB action risk classification; defaulting to destructive', { action });
    return 'destructive';
  }

  requiresAuthorityGrant(action: ActionType): boolean {
    const riskLevel = this.getRiskLevel(action);
    return (
      riskLevel !== 'safe'
      || this.isSensitiveReadAction(action)
      || action === 'sendEmail'
      || this.isCredentialSensitiveAction(action)
    );
  }

  private expectedGrantFlags(
    action: ActionType,
    riskLevel: RiskLevel,
  ): NonNullable<UABGrantValidationContext['expectedFlags']> {
    return {
      mutating: riskLevel !== 'safe',
      destructive: riskLevel === 'destructive',
      sensitive_read: this.isSensitiveReadAction(action),
      external_submission: action === 'sendEmail',
      credential_sensitive: this.isCredentialSensitiveAction(action),
    };
  }

  private isSensitiveReadAction(action: ActionType): boolean {
    return [
      'screenshot',
      'readDocument',
      'readCell',
      'readRange',
      'readFormula',
      'readSlides',
      'readSlideText',
      'readEmails',
      'getCookies',
      'getLocalStorage',
      'getSessionStorage',
    ].includes(action);
  }

  private isCredentialSensitiveAction(action: ActionType): boolean {
    return [
      'getCookies',
      'setCookie',
      'deleteCookie',
      'clearCookies',
      'getLocalStorage',
      'setLocalStorage',
      'deleteLocalStorage',
      'clearLocalStorage',
      'getSessionStorage',
      'setSessionStorage',
      'deleteSessionStorage',
      'clearSessionStorage',
      'executeScript',
    ].includes(action);
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
