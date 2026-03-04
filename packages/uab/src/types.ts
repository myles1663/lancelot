/**
 * Universal App Bridge — Unified API Type Definitions
 *
 * Every framework plugin maps its native UI tree into these types,
 * giving agents a single consistent interface to any desktop app.
 *
 * This module is framework-agnostic — it can be imported by
 * ClaudeClaw, Lancelot, or any other AI agent runtime.
 */

// ─── Core UI Element ────────────────────────────────────────────

export interface UIElement {
  id: string;
  type: ElementType;
  label: string;
  properties: Record<string, unknown>;
  bounds: Bounds;
  children: UIElement[];
  actions: ActionType[];
  visible: boolean;
  enabled: boolean;
  meta?: Record<string, unknown>;
}

export interface Bounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type ElementType =
  | 'window' | 'button' | 'textfield' | 'textarea'
  | 'checkbox' | 'radio' | 'select' | 'menu' | 'menuitem'
  | 'list' | 'listitem' | 'table' | 'tablerow' | 'tablecell'
  | 'tab' | 'tabpanel' | 'tree' | 'treeitem'
  | 'slider' | 'progressbar' | 'scrollbar'
  | 'toolbar' | 'statusbar' | 'dialog' | 'tooltip'
  | 'image' | 'link' | 'label' | 'heading'
  | 'separator' | 'container' | 'unknown';

export type ActionType =
  | 'click' | 'doubleclick' | 'rightclick'
  | 'type' | 'clear' | 'select' | 'scroll'
  | 'focus' | 'hover' | 'expand' | 'collapse'
  | 'invoke' | 'check' | 'uncheck' | 'toggle'
  | 'keypress' | 'hotkey'
  | 'minimize' | 'maximize' | 'restore' | 'close'
  | 'move' | 'resize' | 'screenshot' | 'contextmenu'
  | 'readDocument' | 'readCell' | 'writeCell'
  | 'readRange' | 'writeRange' | 'getSheets' | 'readFormula'
  | 'readSlides' | 'readSlideText'
  | 'readEmails' | 'composeEmail' | 'sendEmail';

// ─── Query / Selector ───────────────────────────────────────────

export interface ElementSelector {
  type?: ElementType;
  label?: string;
  labelExact?: string;
  labelRegex?: string;
  properties?: Record<string, unknown>;
  visible?: boolean;
  enabled?: boolean;
  maxDepth?: number;
  limit?: number;
}

// ─── Actions ────────────────────────────────────────────────────

export interface ActionParams {
  text?: string;
  value?: string;
  direction?: 'up' | 'down' | 'left' | 'right';
  amount?: number;
  method?: string;
  args?: unknown[];
  modifiers?: ('ctrl' | 'shift' | 'alt' | 'meta')[];
  // Keyboard / hotkey params
  key?: string;           // Virtual key name: 'Enter', 'Tab', 'Escape', 'F1', etc.
  keys?: string[];        // Hotkey combo: ['ctrl', 'shift', 's']
  // Window management params
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  // Screenshot params
  outputPath?: string;    // File path for screenshot output
  // Office-specific params
  row?: number;           // Excel row (1-based)
  col?: number;           // Excel column (1-based)
  sheet?: string;         // Excel sheet name
  cellRange?: string;     // Excel range like 'A1:B5'
  // Excel COM params
  formula?: string;       // Excel formula like '=SUM(A1:A10)'
  values?: string[][];    // 2D array for writeRange
  // Outlook COM params
  to?: string;            // Email recipient
  subject?: string;       // Email subject
  body?: string;          // Email body
  cc?: string;            // CC recipients
  folder?: string;        // Outlook folder name (Inbox, Sent, etc.)
  count?: number;         // Number of items to return
  // PowerPoint COM params
  slideIndex?: number;    // Slide number (1-based)
}

export interface ActionResult {
  success: boolean;
  result?: unknown;
  stateChanges?: UIElement[];
  error?: string;
}

// ─── App State ──────────────────────────────────────────────────

export interface AppState {
  window: {
    title: string;
    size: { width: number; height: number };
    position: { x: number; y: number };
    focused: boolean;
  };
  activeElement?: UIElement;
  modals: UIElement[];
  menus: UIElement[];
  clipboard?: string;
}

// ─── Events / Subscriptions ─────────────────────────────────────

export type UABEventType =
  | 'elementChanged' | 'treeChanged' | 'stateChanged' | 'dataChanged';

export interface UABEvent {
  type: UABEventType;
  timestamp: number;
  element?: UIElement;
  changes?: Record<string, { old: unknown; new: unknown }>;
}

export type UABEventCallback = (event: UABEvent) => void;

export interface Subscription {
  id: string;
  event: UABEventType;
  unsubscribe: () => void;
}

// ─── Framework Detection ────────────────────────────────────────

export type FrameworkType =
  | 'electron' | 'qt5' | 'qt6' | 'gtk3' | 'gtk4'
  | 'macos-native' | 'wpf' | 'winui' | 'dotnet'
  | 'flutter' | 'java-swing' | 'javafx' | 'office' | 'unknown';

export interface DetectedApp {
  pid: number;
  name: string;
  path: string;
  framework: FrameworkType;
  confidence: number;
  connectionInfo?: Record<string, unknown>;
  windowTitle?: string;
}

// ─── Plugin Interface ───────────────────────────────────────────

export interface FrameworkPlugin {
  readonly framework: FrameworkType;
  readonly name: string;
  canHandle(app: DetectedApp): boolean;
  connect(app: DetectedApp): Promise<PluginConnection>;
}

export interface PluginConnection {
  readonly app: DetectedApp;
  readonly connected: boolean;
  enumerate(): Promise<UIElement[]>;
  query(selector: ElementSelector): Promise<UIElement[]>;
  act(elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult>;
  state(): Promise<AppState>;
  subscribe(event: UABEventType, callback: UABEventCallback): Promise<Subscription>;
  disconnect(): Promise<void>;
}

// ─── Control Router ─────────────────────────────────────────────

export type ControlMethod = 'direct-api' | 'uab-hook' | 'accessibility' | 'vision';

export interface ControlRoute {
  app: DetectedApp;
  method: ControlMethod;
  connection: PluginConnection;
  fallbacks: ControlMethod[];
}
