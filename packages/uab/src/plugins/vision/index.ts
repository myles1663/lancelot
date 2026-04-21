/**
 * Vision Plugin — Screenshot + Coordinate-based Universal Fallback
 *
 * Priority 4 (last resort) in the UAB control router.
 * Works like Anthropic's computer use tool:
 *
 *   1. Capture screenshot of target window
 *   2. Send to Claude Vision API for element detection
 *   3. Map detected elements to UIElement[] with bounding boxes
 *   4. Execute actions via coordinate-based input injection
 *
 * This works with ANY application — no accessibility API, no framework
 * hooks, no special setup. Just eyes and a mouse.
 *
 * Trade-offs:
 *   - Expensive (API call per enumerate/query)
 *   - Slower (screenshot + API round-trip + input injection)
 *   - Less precise than native APIs
 *   - But UNIVERSAL — works when nothing else does
 */

import type {
  FrameworkPlugin,
  PluginConnection,
  DetectedApp,
  UIElement,
  ElementSelector,
  ActionType,
  ActionParams,
  ActionResult,
  AppState,
  UABEventType,
  UABEventCallback,
  Subscription,
} from '../../types.js';

import { VisionAnalyzer, type VisionAnalyzerOptions } from './analyzer.js';
import {
  clickAt,
  doubleClickAt,
  rightClickAt,
  hoverAt,
  dragPath,
  scrollAt,
  typeTextAt,
  sendKeypress,
  sendHotkey,
  windowAction,
  captureScreenshot,
  getWindowBounds,
} from './input.js';

// ─── Plugin ──────────────────────────────────────────────────

export class VisionPlugin implements FrameworkPlugin {
  readonly framework = 'unknown' as const;
  readonly controlMethod = 'vision' as const;
  readonly name = 'Vision (Screenshot + Coordinates)';

  private analyzerOptions?: VisionAnalyzerOptions;

  constructor(options?: VisionAnalyzerOptions) {
    this.analyzerOptions = options;
  }

  /**
   * Vision can handle ANY app — it's the universal fallback.
   * But only if the API key is configured.
   */
  canHandle(_app: DetectedApp): boolean {
    const analyzer = new VisionAnalyzer(this.analyzerOptions);
    return analyzer.available;
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    const analyzer = new VisionAnalyzer(this.analyzerOptions);

    if (!analyzer.available) {
      throw new Error(
        'Vision fallback requires ANTHROPIC_API_KEY. Set it in .env or environment.'
      );
    }

    // Verify we can see the window
    const bounds = getWindowBounds(app.pid);
    if (!bounds.success) {
      throw new Error(`Cannot locate window for PID ${app.pid}: ${bounds.error}`);
    }

    return new VisionConnection(app, analyzer);
  }
}

// ─── Connection ──────────────────────────────────────────────

/** Cache TTL for vision analysis results (ms) */
const ANALYSIS_CACHE_TTL = 8000;

class VisionConnection implements PluginConnection {
  readonly app: DetectedApp;
  private _connected = true;
  private analyzer: VisionAnalyzer;

  // Cache last analysis to avoid re-analyzing for rapid enumerate→query sequences
  private cachedElements: UIElement[] | null = null;
  private cacheTimestamp = 0;
  private cachedWindowBounds: { x: number; y: number; width: number; height: number } | null = null;

  constructor(app: DetectedApp, analyzer: VisionAnalyzer) {
    this.app = app;
    this.analyzer = analyzer;
  }

  get connected(): boolean {
    return this._connected;
  }

  /**
   * Enumerate all visible UI elements via vision analysis.
   * Takes a screenshot → sends to Claude Vision → returns UIElements.
   */
  async enumerate(): Promise<UIElement[]> {
    this.ensureConnected();

    // Use cache if fresh
    if (this.cachedElements && Date.now() - this.cacheTimestamp < ANALYSIS_CACHE_TTL) {
      return this.cachedElements;
    }

    // Capture screenshot
    const screenshotPath = `data/screenshots/vision-${this.app.pid}-${Date.now()}.png`;
    const capture = captureScreenshot(this.app.pid, screenshotPath);

    if (!capture.success || !capture.base64) {
      throw new Error(`Screenshot capture failed: ${capture.error || 'no image data'}`);
    }

    // Get window bounds for coordinate mapping
    const bounds = getWindowBounds(this.app.pid);
    if (!bounds.success) {
      throw new Error(`Cannot get window bounds: ${bounds.error}`);
    }

    const windowBounds = {
      x: bounds.x!,
      y: bounds.y!,
      width: bounds.width!,
      height: bounds.height!,
    };

    // Analyze with Claude Vision
    const elements = await this.analyzer.analyze(capture.base64, windowBounds);

    // Cache results
    this.cachedElements = elements;
    this.cacheTimestamp = Date.now();
    this.cachedWindowBounds = windowBounds;

    return elements;
  }

  /**
   * Query elements matching a selector.
   * Runs enumerate() first, then filters locally.
   */
  async query(selector: ElementSelector): Promise<UIElement[]> {
    const all = await this.enumerate();
    return this.filterElements(all, selector);
  }

  /**
   * Perform an action on a UI element identified by vision.
   * Resolves element → gets center coordinates → injects input.
   */
  async act(elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    this.ensureConnected();

    // Handle window-level actions that don't need an element
    if (!elementId || elementId === '') {
      return this.handleWindowAction(action, params);
    }

    // Look up element from cache
    const element = this.findElement(elementId);
    if (!element) {
      // Cache might be stale — try re-enumerating
      await this.enumerate();
      const retryElement = this.findElement(elementId);
      if (!retryElement) {
        return { success: false, error: `Element "${elementId}" not found in vision analysis` };
      }
      return this.executeAction(retryElement, action, params);
    }

    return this.executeAction(element, action, params);
  }

  /**
   * Get current app state.
   */
  async state(): Promise<AppState> {
    this.ensureConnected();

    const bounds = getWindowBounds(this.app.pid);
    if (!bounds.success) {
      throw new Error(`Cannot get window state: ${bounds.error}`);
    }

    return {
      window: {
        title: bounds.title || this.app.name,
        size: { width: bounds.width!, height: bounds.height! },
        position: { x: bounds.x!, y: bounds.y! },
        focused: true, // Can't determine focus via vision
      },
      modals: [],
      menus: [],
    };
  }

  /**
   * Event subscription is not supported by vision fallback.
   * Vision is stateless — it can only observe snapshots.
   */
  async subscribe(_event: UABEventType, _callback: UABEventCallback): Promise<Subscription> {
    const id = `vision-nosub-${Date.now()}`;
    return {
      id,
      event: _event,
      unsubscribe: () => {},
    };
  }

  async disconnect(): Promise<void> {
    this._connected = false;
    this.cachedElements = null;
    this.cachedWindowBounds = null;
  }

  // ─── Internal ────────────────────────────────────────────────

  private ensureConnected(): void {
    if (!this._connected) {
      throw new Error('Vision connection is disconnected');
    }
  }

  private findElement(elementId: string): UIElement | null {
    if (!this.cachedElements) return null;
    return this.cachedElements.find(el => el.id === elementId) || null;
  }

  /**
   * Execute an action on a specific element using coordinate-based input.
   */
  private executeAction(element: UIElement, action: ActionType, params?: ActionParams): ActionResult {
    const cx = Math.round(element.bounds.x + element.bounds.width / 2);
    const cy = Math.round(element.bounds.y + element.bounds.height / 2);

    // Invalidate cache on mutating actions
    this.invalidateCache();

    switch (action) {
      case 'click':
        return clickAt(this.app.pid, cx, cy);

      case 'doubleclick':
        return doubleClickAt(this.app.pid, cx, cy);

      case 'rightclick':
        return rightClickAt(this.app.pid, cx, cy);

      case 'hover':
        return hoverAt(this.app.pid, cx, cy);

      case 'drag': {
        const btn = params?.button || 'left';
        // Drag from element center to target, or along a path
        if (params?.dragPath && Array.isArray(params.dragPath)) {
          return dragPath(this.app.pid, params.dragPath, params.stepDelay || 10, btn);
        }
        if (params?.toX !== undefined && params?.toY !== undefined) {
          return dragPath(this.app.pid, [
            { x: cx, y: cy },
            { x: params.toX, y: params.toY },
          ], params.stepDelay || 10, btn);
        }
        return { success: false, error: 'Drag requires either dragPath:[{x,y},...] or toX/toY params' };
      }

      case 'type':
        if (!params?.text) return { success: false, error: 'No text provided' };
        return typeTextAt(this.app.pid, cx, cy, params.text);

      case 'clear':
        // Click to focus, then select all + delete
        clickAt(this.app.pid, cx, cy);
        return sendHotkey(this.app.pid, ['ctrl', 'a']);

      case 'focus':
        return clickAt(this.app.pid, cx, cy);

      case 'select':
        return clickAt(this.app.pid, cx, cy);

      case 'check':
      case 'uncheck':
      case 'toggle':
        return clickAt(this.app.pid, cx, cy);

      case 'expand':
      case 'collapse':
        return clickAt(this.app.pid, cx, cy);

      case 'keypress':
        if (!params?.key) return { success: false, error: 'No key provided' };
        clickAt(this.app.pid, cx, cy); // Focus first
        return sendKeypress(this.app.pid, params.key);

      case 'hotkey':
        if (!params?.keys) return { success: false, error: 'No keys provided' };
        return sendHotkey(this.app.pid, params.keys);

      case 'scroll': {
        // Use mouse wheel at element position
        const dir = params?.direction || 'down';
        const key = dir === 'up' ? 'pageup' : dir === 'down' ? 'pagedown' : dir === 'left' ? 'left' : 'right';
        clickAt(this.app.pid, cx, cy);
        return sendKeypress(this.app.pid, key);
      }

      default:
        return this.handleWindowAction(action, params);
    }
  }

  /**
   * Handle window-level actions (minimize, maximize, screenshot, keyboard, etc.)
   */
  private handleWindowAction(action: ActionType, params?: ActionParams): ActionResult {
    switch (action) {
      case 'minimize':
        return windowAction(this.app.pid, 'minimize');
      case 'maximize':
        return windowAction(this.app.pid, 'maximize');
      case 'restore':
        return windowAction(this.app.pid, 'restore');
      case 'close':
        return windowAction(this.app.pid, 'close');

      case 'screenshot': {
        const outPath = params?.outputPath || `data/screenshots/vision-${this.app.pid}-${Date.now()}.png`;
        const result = captureScreenshot(this.app.pid, outPath);
        if (result.success) {
          return { success: true, result: result.path };
        }
        return { success: false, error: result.error };
      }

      case 'keypress':
        if (!params?.key) return { success: false, error: 'No key provided' };
        return sendKeypress(this.app.pid, params.key);

      case 'hotkey':
        if (!params?.keys) return { success: false, error: 'No keys provided' };
        return sendHotkey(this.app.pid, params.keys);

      default:
        return { success: false, error: `Action "${action}" not supported by vision fallback` };
    }
  }

  /**
   * Filter elements by selector criteria.
   */
  private filterElements(elements: UIElement[], selector: ElementSelector): UIElement[] {
    return elements.filter(el => {
      if (selector.type && el.type !== selector.type) return false;
      if (selector.visible !== undefined && el.visible !== selector.visible) return false;
      if (selector.enabled !== undefined && el.enabled !== selector.enabled) return false;

      if (selector.label) {
        if (!el.label.toLowerCase().includes(selector.label.toLowerCase())) return false;
      }
      if (selector.labelExact) {
        if (el.label !== selector.labelExact) return false;
      }
      if (selector.labelRegex) {
        try {
          if (!new RegExp(selector.labelRegex, 'i').test(el.label)) return false;
        } catch { return false; }
      }

      if (selector.properties) {
        for (const [key, value] of Object.entries(selector.properties)) {
          if (el.properties[key] !== value) return false;
        }
      }

      return true;
    }).slice(0, selector.limit || Infinity);
  }

  /**
   * Invalidate the analysis cache (after mutating actions).
   */
  private invalidateCache(): void {
    this.cachedElements = null;
    this.cacheTimestamp = 0;
  }
}

export { VisionAnalyzer, type VisionAnalyzerOptions } from './analyzer.js';
