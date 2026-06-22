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

// ─── Framework-Independent Connector ─────────────────────────────
export { UABConnector } from './connector.js';
export type { ConnectorOptions, ConnectionInfo } from './connector.js';

// ─── App Registry ────────────────────────────────────────────────
export { AppRegistry } from './registry.js';
export type { AppProfile, RegistrySnapshot, RegistryOptions } from './registry.js';

// ─── Core Service (Kai integration) ──────────────────────────────
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
  FrameworkHookDescriptor,
  HookControlMethod,
  FrameworkPlugin,
  PluginConnection,
  ControlMethod,
  ConcertoMethod,
  ConcertoMethodDescriptor,
  OperationPlan,
  OperationPlanContext,
  DirectApiConfig,
  ControlRoute,
  FocusedElementInfo,
  PathSelector,
  StateChangeEvent,
  StateChangeCallback,
  AtomicStep,
  AtomicChainDef,
  AtomicChainResult,
  SmartResolveResult,
} from './types.js';

export { FRAMEWORK_HOOKS, describeControlMethod } from './hooks.js';
export { getConcertoMethodInventory, planOperation } from './concerto.js';

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
export {
  canonicalGrantPayload,
  signUABAuthorityGrant,
  signUABGrantPayload,
  validateUABAuthorityGrant,
} from './governance/grants.js';
export type {
  UABAuthorityGrant,
  UABGrantReasonCode,
  UABGrantValidationContext,
  UABGrantValidationResult,
  UABRiskLabel,
} from './governance/grants.js';

export { withRetry, isRetryable, retryable, withTimeout } from './retry.js';
export type { RetryOptions } from './retry.js';

// ─── Spatial Map & Composite Engine ─────────────────────────────
export { buildSpatialMap, SpatialIndex, renderTextMap, renderJsonMap } from './spatial.js';
export type { SpatialElement, SpatialRow, SpatialMap, SpatialQuery, NearestResult } from './spatial.js';

export { CompositeEngine } from './composite.js';
export type { CompositeResult, CompositeOptions, UABLike } from './composite.js';

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
export {
  OfficePlugin,
  buildExcelWorkbookBenchmarkScript,
  probeExcelAutomation,
  resolveExcelWorkbookBenchmarkPaths,
  runExcelWorkbookBenchmark,
} from './plugins/office/index.js';
export type {
  ExcelAutomationProbeResult,
  ExcelWorkbookBenchmarkManifest,
  ExcelWorkbookBenchmarkOptions,
  ExcelWorkbookBenchmarkPaths,
  ExcelWorkbookBenchmarkResult,
} from './plugins/office/index.js';
export { DirectApiPlugin } from './plugins/direct-api/index.js';
export { BrowserPlugin, isBrowserProcess, getBrowserDisplayName } from './plugins/browser/index.js';
export { ChromeExtPlugin } from './plugins/chrome-ext/index.js';
export { ExtensionWSServer } from './plugins/chrome-ext/ws-server.js';
export { VisionPlugin } from './plugins/vision/index.js';

// ─── Environment Detection ──────────────────────────────────────
export { detectEnvironment, getDefaults, resetEnvironment, env } from './environment.js';
export type { RuntimeMode, EnvironmentInfo, EnvironmentDefaults } from './environment.js';

// ─── HTTP Server (Server-Side Access) ───────────────────────────
export { UABServer } from './server.js';
export type { ServerOptions } from './server.js';

// ─── Agent SDK ─────────────────────────────────────────────────
export { AgentSDK, desktop } from './sdk.js';

// ─── Agent Prompt Templates ────────────────────────────────────
export { getAgentPrompt, getClaudeMdSnippet, getMcpConfig } from './agent-prompt.js';
export type { PromptMode, PromptOptions } from './agent-prompt.js';

// ─── Logger ─────────────────────────────────────────────────────
export { createLogger, closeLogger } from './logger.js';
