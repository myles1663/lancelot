/**
 * UAB Agent SDK — Dead-simple wrapper that's easier to use than screenshots.
 *
 * The whole point: if using UAB is EASIER than taking a screenshot,
 * agents will naturally prefer it. This SDK makes the common cases trivial.
 *
 * @example
 * ```ts
 * import { desktop } from './sdk.js';
 *
 * // One-liner: click a button in any app
 * await desktop.click('Notepad', 'File');
 *
 * // Type into a field
 * await desktop.type('Notepad', 'Edit area', 'Hello world');
 *
 * // Get what's on screen (no screenshot needed)
 * const layout = await desktop.look('Notepad');
 *
 * // Full workflow
 * await desktop.do('Notepad', [
 *   { click: 'File' },
 *   { click: 'Save As...' },
 *   { type: { field: 'File name', text: 'document.txt' } },
 *   { click: 'Save' },
 * ]);
 * ```
 */

import { UABConnector } from './connector.js';
import type { UIElement, ActionResult } from './types.js';

// ─── SDK Types ─────────────────────────────────────────────────

interface ClickStep { click: string }
interface TypeStep { type: { field: string; text: string } }
interface HotkeyStep { hotkey: string }
interface KeyStep { key: string }
interface WaitStep { wait: number }

type WorkflowStep = ClickStep | TypeStep | HotkeyStep | KeyStep | WaitStep;

interface AppHandle {
  pid: number;
  name: string;
}

// ─── Agent SDK ─────────────────────────────────────────────────

export class AgentSDK {
  private connector: UABConnector;
  private started = false;
  private connected = new Map<string, number>(); // name → pid cache

  constructor() {
    this.connector = new UABConnector({ persistent: false });
  }

  /** Ensure connector is running */
  private async ensureStarted(): Promise<void> {
    if (!this.started) {
      await this.connector.start();
      this.started = true;
    }
  }

  /** Resolve app name to PID, connecting if needed */
  private async resolve(nameOrPid: string | number): Promise<number> {
    await this.ensureStarted();

    if (typeof nameOrPid === 'number') {
      if (!this.connector.isConnected(nameOrPid)) {
        await this.connector.connect(nameOrPid);
      }
      return nameOrPid;
    }

    // Check cache
    const cached = this.connected.get(nameOrPid.toLowerCase());
    if (cached && this.connector.isConnected(cached)) return cached;

    // Scan if needed, then connect
    const apps = this.connector.apps();
    if (apps.length === 0) await this.connector.scan();

    const info = await this.connector.connect(nameOrPid);
    this.connected.set(nameOrPid.toLowerCase(), info.pid);
    return info.pid;
  }

  // ─── Simple Operations ─────────────────────────────────────

  /**
   * Look at an app's UI — returns structured text layout.
   * This replaces screenshots for 90% of use cases.
   */
  async look(app: string | number): Promise<string> {
    const pid = await this.resolve(app);
    return await this.connector.textMap(pid, 'compact');
  }

  /**
   * Click an element by name. Uses smart 6-method fallback.
   */
  async click(app: string | number, elementName: string): Promise<{ success: boolean; method: string }> {
    const pid = await this.resolve(app);
    return await this.connector.smartInvoke(pid, elementName);
  }

  /**
   * Type text into a field. Finds the field by name, clears it, and types.
   */
  async type(app: string | number, fieldName: string, text: string): Promise<ActionResult> {
    const pid = await this.resolve(app);
    const elements = await this.connector.query(pid, { label: fieldName, limit: 1 });
    if (elements.length === 0) {
      throw new Error(`Field "${fieldName}" not found`);
    }
    // Clear then type
    await this.connector.act(pid, elements[0].id, 'clear');
    return await this.connector.act(pid, elements[0].id, 'type', { text });
  }

  /**
   * Send a keyboard shortcut (e.g., "ctrl+s", "alt+f4").
   */
  async shortcut(app: string | number, keys: string): Promise<ActionResult> {
    const pid = await this.resolve(app);
    return await this.connector.hotkey(pid, keys);
  }

  /**
   * Send a single key press (e.g., "enter", "escape", "tab").
   */
  async key(app: string | number, key: string): Promise<ActionResult> {
    const pid = await this.resolve(app);
    return await this.connector.keypress(pid, key);
  }

  /**
   * Find UI elements matching criteria.
   */
  async find(app: string | number, options: {
    type?: string;
    label?: string;
    visible?: boolean;
  } = {}): Promise<UIElement[]> {
    const pid = await this.resolve(app);
    return await this.connector.query(pid, options as any);
  }

  /**
   * Get current app state (window position, title, focused element).
   */
  async status(app: string | number): Promise<any> {
    const pid = await this.resolve(app);
    return await this.connector.state(pid);
  }

  /**
   * Get list of running apps.
   */
  async apps(): Promise<AppHandle[]> {
    await this.ensureStarted();
    const cached = this.connector.apps();
    if (cached.length === 0) await this.connector.scan();
    return this.connector.apps().map(a => ({ pid: a.pid!, name: a.name }));
  }

  // ─── Workflow Engine ───────────────────────────────────────

  /**
   * Execute a multi-step workflow in plain English.
   *
   * @example
   * ```ts
   * await desktop.do('Notepad', [
   *   { click: 'File' },
   *   { click: 'Save As...' },
   *   { type: { field: 'File name', text: 'doc.txt' } },
   *   { click: 'Save' },
   * ]);
   * ```
   */
  async do(app: string | number, steps: WorkflowStep[]): Promise<{
    success: boolean;
    stepsCompleted: number;
    results: Array<{ step: number; success: boolean; error?: string }>;
  }> {
    const pid = await this.resolve(app);
    const results: Array<{ step: number; success: boolean; error?: string }> = [];

    for (let i = 0; i < steps.length; i++) {
      const step = steps[i];
      try {
        if ('click' in step) {
          await this.connector.smartInvoke(pid, step.click);
        } else if ('type' in step) {
          const elements = await this.connector.query(pid, { label: step.type.field, limit: 1 });
          if (elements.length === 0) throw new Error(`Field "${step.type.field}" not found`);
          await this.connector.act(pid, elements[0].id, 'clear');
          await this.connector.act(pid, elements[0].id, 'type', { text: step.type.text });
        } else if ('hotkey' in step) {
          await this.connector.hotkey(pid, step.hotkey);
        } else if ('key' in step) {
          await this.connector.keypress(pid, step.key);
        } else if ('wait' in step) {
          await new Promise(r => setTimeout(r, step.wait));
        }
        results.push({ step: i, success: true });
      } catch (err) {
        const error = err instanceof Error ? err.message : String(err);
        results.push({ step: i, success: false, error });
        return {
          success: false,
          stepsCompleted: i,
          results,
        };
      }
    }

    return {
      success: true,
      stepsCompleted: steps.length,
      results,
    };
  }

  // ─── Lifecycle ─────────────────────────────────────────────

  /**
   * Shut down the SDK and disconnect from all apps.
   */
  async shutdown(): Promise<void> {
    if (this.started) {
      await this.connector.stop();
      this.started = false;
      this.connected.clear();
    }
  }
}

// ─── Default Instance ──────────────────────────────────────────

/** Pre-configured SDK instance — just import and use */
export const desktop = new AgentSDK();
