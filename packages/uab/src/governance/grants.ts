import { createHmac, timingSafeEqual } from 'crypto';
import type { ActionType } from '../types.js';

export type UABRiskLabel = 'safe' | 'moderate' | 'destructive';

export interface UABAuthorityGrant {
  grant_id: string;
  issued_at: string;
  expires_at: string;
  nonce: string;
  risk_tier: string;
  uab_risk: string;
  capability: string;
  app_name: string;
  app_pid?: number | null;
  action: string;
  selector_scope: string;
  sensitive_read: boolean;
  mutating: boolean;
  destructive: boolean;
  external_submission: boolean;
  credential_sensitive: boolean;
  policy_version: string;
  soul_version: string;
  workflow_id: string;
  run_id: string;
  parent_receipt_id?: string | null;
  approval_id?: string | null;
  signature?: string;
}

export type UABGrantReasonCode =
  | 'valid'
  | 'grant_not_supplied'
  | 'missing_authority_secret'
  | 'invalid_grant_payload'
  | 'missing_required_field'
  | 'missing_signature'
  | 'unknown_uab_risk'
  | 'invalid_nonce'
  | 'invalid_expiration'
  | 'grant_expired'
  | 'invalid_signature'
  | 'replayed_nonce'
  | 'wrong_app'
  | 'wrong_pid'
  | 'wrong_action'
  | 'missing_selector_scope'
  | 'wrong_selector_scope'
  | 'flag_mismatch';

export interface UABGrantValidationResult {
  valid: boolean;
  reasonCode: UABGrantReasonCode;
  reason: string;
  field?: string;
  expected?: unknown;
  actual?: unknown;
}

export interface UABGrantValidationContext {
  appName?: string;
  appPid?: number;
  action?: ActionType | string;
  selectorScope?: string;
  expectedFlags?: Partial<Pick<
    UABAuthorityGrant,
    'sensitive_read' | 'mutating' | 'destructive' | 'external_submission' | 'credential_sensitive'
  >>;
  now?: Date;
}

const REQUIRED_FIELDS = [
  'grant_id',
  'issued_at',
  'expires_at',
  'nonce',
  'risk_tier',
  'uab_risk',
  'capability',
  'app_name',
  'action',
  'policy_version',
  'soul_version',
] as const;

const UAB_RISK_LABELS = new Set<UABRiskLabel>(['safe', 'moderate', 'destructive']);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function invalid(
  reasonCode: UABGrantReasonCode,
  reason: string,
  extra: Omit<UABGrantValidationResult, 'valid' | 'reasonCode' | 'reason'> = {},
): UABGrantValidationResult {
  return { valid: false, reasonCode, reason, ...extra };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isMissing(value: unknown): boolean {
  return value === undefined || value === null || value === '';
}

function parseDate(value: unknown): Date | null {
  if (typeof value !== 'string' || value.length === 0) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function canonicalize(value: unknown): string {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(item => canonicalize(item)).join(',')}]`;
  }

  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map(key => `${JSON.stringify(key)}:${canonicalize(record[key])}`)
    .join(',')}}`;
}

export function canonicalGrantPayload(grant: UABAuthorityGrant | Record<string, unknown>): string {
  const payload: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(grant)) {
    if (key !== 'signature' && value !== undefined) {
      payload[key] = value;
    }
  }
  return canonicalize(payload);
}

export function signUABGrantPayload(payload: string, secret: string | Buffer): string {
  return createHmac('sha256', secret).update(payload, 'utf8').digest('hex');
}

export function signUABAuthorityGrant(grant: UABAuthorityGrant, secret: string | Buffer): string {
  return signUABGrantPayload(canonicalGrantPayload(grant), secret);
}

function signaturesMatch(actual: string, expected: string): boolean {
  const actualBuffer = Buffer.from(actual, 'hex');
  const expectedBuffer = Buffer.from(expected, 'hex');
  return actualBuffer.length === expectedBuffer.length && timingSafeEqual(actualBuffer, expectedBuffer);
}

export function validateUABAuthorityGrant(
  grant: unknown,
  secret: string | Buffer | undefined,
  context: UABGrantValidationContext = {},
): UABGrantValidationResult {
  if (grant === undefined || grant === null) {
    return { valid: true, reasonCode: 'grant_not_supplied', reason: 'No UAB authority grant supplied' };
  }
  if (!isRecord(grant)) {
    return invalid('invalid_grant_payload', 'UAB authority grant must be a JSON object');
  }
  if (!secret || (typeof secret === 'string' && secret.length === 0)) {
    return invalid('missing_authority_secret', 'UAB authority grant secret is not configured');
  }

  for (const field of REQUIRED_FIELDS) {
    if (isMissing(grant[field])) {
      return invalid('missing_required_field', `UAB authority grant missing required field: ${field}`, { field });
    }
  }

  if (typeof grant.signature !== 'string' || grant.signature.length === 0) {
    return invalid('missing_signature', 'UAB authority grant missing signature', { field: 'signature' });
  }
  if (typeof grant.uab_risk !== 'string' || !UAB_RISK_LABELS.has(grant.uab_risk as UABRiskLabel)) {
    return invalid('unknown_uab_risk', `Unknown UAB risk label: ${String(grant.uab_risk)}`, {
      field: 'uab_risk',
      actual: grant.uab_risk,
    });
  }
  if (typeof grant.nonce !== 'string' || !UUID_PATTERN.test(grant.nonce)) {
    return invalid('invalid_nonce', 'UAB authority grant nonce must be a UUID', {
      field: 'nonce',
      actual: grant.nonce,
    });
  }

  const expiresAt = parseDate(grant.expires_at);
  if (!expiresAt) {
    return invalid('invalid_expiration', 'UAB authority grant expires_at must be an ISO timestamp', {
      field: 'expires_at',
      actual: grant.expires_at,
    });
  }
  const now = context.now ?? new Date();
  if (now.getTime() >= expiresAt.getTime()) {
    return invalid('grant_expired', 'UAB authority grant is expired', {
      field: 'expires_at',
      actual: grant.expires_at,
    });
  }

  const expectedSignature = signUABGrantPayload(canonicalGrantPayload(grant), secret);
  if (!signaturesMatch(grant.signature, expectedSignature)) {
    return invalid('invalid_signature', 'UAB authority grant signature is invalid', { field: 'signature' });
  }

  if (context.appName !== undefined && grant.app_name !== context.appName) {
    return invalid('wrong_app', 'UAB authority grant app_name does not match target app', {
      field: 'app_name',
      expected: context.appName,
      actual: grant.app_name,
    });
  }
  if (context.appPid !== undefined && grant.app_pid !== undefined && grant.app_pid !== null && grant.app_pid !== context.appPid) {
    return invalid('wrong_pid', 'UAB authority grant app_pid does not match target PID', {
      field: 'app_pid',
      expected: context.appPid,
      actual: grant.app_pid,
    });
  }
  if (context.action !== undefined && grant.action !== context.action) {
    return invalid('wrong_action', 'UAB authority grant action does not match requested action', {
      field: 'action',
      expected: context.action,
      actual: grant.action,
    });
  }
  if (typeof grant.selector_scope === 'string' && grant.selector_scope.length > 0) {
    if (context.selectorScope === undefined) {
      return invalid('missing_selector_scope', 'UAB authority grant selector_scope requires a target selector scope', {
        field: 'selector_scope',
      });
    }
    if (grant.selector_scope !== context.selectorScope) {
      return invalid('wrong_selector_scope', 'UAB authority grant selector_scope does not match target scope', {
        field: 'selector_scope',
        expected: context.selectorScope,
        actual: grant.selector_scope,
      });
    }
  }

  for (const [field, expected] of Object.entries(context.expectedFlags ?? {})) {
    const actual = grant[field];
    if (actual !== expected) {
      return invalid('flag_mismatch', `UAB authority grant ${field} flag does not match requested action`, {
        field,
        expected,
        actual,
      });
    }
  }

  return { valid: true, reasonCode: 'valid', reason: 'valid' };
}
