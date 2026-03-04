/**
 * Universal App Bridge (UAB) — Framework-level desktop app control for AI agents.
 *
 * Hook into UI frameworks (Electron, Qt, GTK, WPF, Flutter, Java, Office)
 * to get structured, reliable access to any desktop application's interface.
 *
 * @example
 * ```ts
 * import { uab } from 'universal-app-bridge';
 *
 * await uab.start();
 * const apps = await uab.detect();
 * await uab.connect(apps[0]);
 * const buttons = await uab.query(apps[0].pid, { type: 'button' });
 * await uab.act(apps[0].pid, buttons[0].id, 'click');
 * await uab.stop();
 * ```
 *
 * @packageDocumentation
 */

// ─── Core Service ────────────────────────────────────────────────
export { UABService, uab } from './service.js';

// ─── Types ───────────────────────────────────────────────────────
export type {
  UIElement,
  Bounds,
  ElementType,
  ActionType,
  ElementSelector,
  ActionParams,
  ActionResult,
  AppState,
  UABEventType,
  UABEvent,
  UABEventCallback,
  Subscription,
  FrameworkType,
  DetectedApp,
  FrameworkPlugin,
  PluginConnection,
  ControlMethod,
  ControlRoute,
} from './types.js';

// ─── Detection & Routing ────────────────────────────────────────
export { FrameworkDetector } from './detector.js';
export { ControlRouter, RoutedConnection } from './router.js';

// ─── Production Hardening ───────────────────────────────────────
export { ElementCache } from './cache.js';
export type { CacheOptions, CacheStats } from './cache.js';

export { ConnectionManager } from './connection-manager.js';
export type {
  ConnectionEntry,
  ConnectionManagerOptions,
  ConnectionEvent,
  ConnectionEventCallback,
} from './connection-manager.js';

export { PermissionManager } from './permissions.js';
export type {
  RiskLevel,
  PermissionCheck,
  AuditEntry,
  PermissionOptions,
} from './permissions.js';

export { withRetry, isRetryable, retryable, withTimeout } from './retry.js';
export type { RetryOptions } from './retry.js';

// ─── Action Chains ──────────────────────────────────────────────
export { ChainExecutor, buildFormChain, buildMenuChain } from './chains.js';
export type {
  ActionStep,
  WaitStep,
  ConditionalStep,
  DelayStep,
  KeypressStep,
  HotkeyStep,
  TypeTextStep,
  ChainStep,
  ChainDefinition,
  StepResult,
  ChainResult,
} from './chains.js';

// ─── Plugins ────────────────────────────────────────────────────
export { PluginManager } from './plugins/base.js';
export { ElectronPlugin } from './plugins/electron/index.js';
export { WinUIAPlugin } from './plugins/win-uia/index.js';
export { QtPlugin } from './plugins/qt/index.js';
export { GtkPlugin } from './plugins/gtk/index.js';
export { JavaPlugin } from './plugins/java/index.js';
export { FlutterPlugin } from './plugins/flutter/index.js';
export { OfficePlugin } from './plugins/office/index.js';

// ─── Logger ─────────────────────────────────────────────────────
export { createLogger, closeLogger } from './logger.js';
