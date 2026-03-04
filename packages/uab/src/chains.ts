/**
 * UAB Action Chains — Multi-step workflow execution engine.
 *
 * Phase 4: Automation workflows.
 * - Sequential action chains with verification between steps
 * - Wait-for-element conditions (poll until element appears)
 * - Conditional branching (if element exists, do X)
 * - Step-level error handling and rollback
 * - Named chains for reusability
 */

import type {
  UIElement, ElementSelector, ActionType, ActionParams, ActionResult,
} from './types.js';
import type { UABService } from './service.js';
import { createLogger } from './logger.js';

const log = createLogger('uab-chains');

// ─── Step Types ────────────────────────────────────────────────

export interface ActionStep {
  type: 'action';
  /** Element selector to find the target */
  selector: ElementSelector;
  /** Action to perform */
  action: ActionType;
  /** Action parameters */
  params?: ActionParams;
  /** Optional label for logging */
  label?: string;
}

export interface WaitStep {
  type: 'wait';
  /** Element selector to wait for */
  selector: ElementSelector;
  /** Max time to wait in ms (default: 10000) */
  timeoutMs?: number;
  /** Poll interval in ms (default: 500) */
  pollMs?: number;
  /** Wait for element to disappear instead */
  waitForAbsence?: boolean;
  label?: string;
}

export interface ConditionalStep {
  type: 'conditional';
  /** Element selector to check */
  selector: ElementSelector;
  /** Steps to run if element exists */
  ifPresent: ChainStep[];
  /** Steps to run if element does not exist */
  ifAbsent?: ChainStep[];
  label?: string;
}

export interface DelayStep {
  type: 'delay';
  /** Delay in ms */
  ms: number;
  label?: string;
}

export interface KeypressStep {
  type: 'keypress';
  key: string;
  label?: string;
}

export interface HotkeyStep {
  type: 'hotkey';
  keys: string[];
  label?: string;
}

export interface TypeTextStep {
  type: 'typeText';
  /** Element selector to find the target input */
  selector: ElementSelector;
  /** Text to type */
  text: string;
  /** Clear field first */
  clearFirst?: boolean;
  label?: string;
}

export type ChainStep =
  | ActionStep | WaitStep | ConditionalStep
  | DelayStep | KeypressStep | HotkeyStep | TypeTextStep;

// ─── Chain Definition ──────────────────────────────────────────

export interface ChainDefinition {
  /** Human-readable chain name */
  name: string;
  /** Target app PID */
  pid: number;
  /** Ordered steps to execute */
  steps: ChainStep[];
  /** Stop chain on first error (default: true) */
  stopOnError?: boolean;
  /** Delay between steps in ms (default: 200) */
  stepDelay?: number;
}

// ─── Chain Result ──────────────────────────────────────────────

export interface StepResult {
  stepIndex: number;
  step: ChainStep;
  success: boolean;
  result?: ActionResult;
  error?: string;
  durationMs: number;
  skipped?: boolean;
}

export interface ChainResult {
  name: string;
  success: boolean;
  stepsCompleted: number;
  totalSteps: number;
  steps: StepResult[];
  durationMs: number;
  error?: string;
}

// ─── Chain Executor ────────────────────────────────────────────

export class ChainExecutor {
  private uab: UABService;

  constructor(uab: UABService) {
    this.uab = uab;
  }

  /** Execute a chain definition */
  async execute(chain: ChainDefinition): Promise<ChainResult> {
    const startTime = Date.now();
    const stopOnError = chain.stopOnError ?? true;
    const stepDelay = chain.stepDelay ?? 200;
    const stepResults: StepResult[] = [];
    let stepsCompleted = 0;

    log.info('Chain started', { name: chain.name, pid: chain.pid, steps: chain.steps.length });

    for (let i = 0; i < chain.steps.length; i++) {
      const step = chain.steps[i];
      const stepStart = Date.now();

      try {
        const result = await this.executeStep(chain.pid, step);
        stepResults.push({
          stepIndex: i,
          step,
          success: true,
          result: result || undefined,
          durationMs: Date.now() - stepStart,
        });
        stepsCompleted++;
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : String(err);
        log.warn('Chain step failed', {
          name: chain.name,
          step: i,
          type: step.type,
          label: step.label,
          error: errorMsg,
        });

        stepResults.push({
          stepIndex: i,
          step,
          success: false,
          error: errorMsg,
          durationMs: Date.now() - stepStart,
        });

        if (stopOnError) {
          return {
            name: chain.name,
            success: false,
            stepsCompleted,
            totalSteps: chain.steps.length,
            steps: stepResults,
            durationMs: Date.now() - startTime,
            error: `Step ${i} (${step.label || step.type}) failed: ${errorMsg}`,
          };
        }
      }

      // Inter-step delay (skip after last step)
      if (i < chain.steps.length - 1 && stepDelay > 0) {
        await new Promise(r => setTimeout(r, stepDelay));
      }
    }

    const result: ChainResult = {
      name: chain.name,
      success: stepResults.every(s => s.success),
      stepsCompleted,
      totalSteps: chain.steps.length,
      steps: stepResults,
      durationMs: Date.now() - startTime,
    };

    log.info('Chain completed', {
      name: chain.name,
      success: result.success,
      stepsCompleted,
      durationMs: result.durationMs,
    });

    return result;
  }

  /** Execute a single step */
  private async executeStep(pid: number, step: ChainStep): Promise<ActionResult | null> {
    switch (step.type) {
      case 'action':
        return this.executeAction(pid, step);
      case 'wait':
        await this.executeWait(pid, step);
        return null;
      case 'conditional':
        await this.executeConditional(pid, step);
        return null;
      case 'delay':
        await new Promise(r => setTimeout(r, step.ms));
        return null;
      case 'keypress':
        return this.uab.keypress(pid, step.key);
      case 'hotkey':
        return this.uab.hotkey(pid, step.keys);
      case 'typeText':
        return this.executeTypeText(pid, step);
      default:
        throw new Error(`Unknown step type: ${(step as ChainStep).type}`);
    }
  }

  /** Execute an action step — find element, then act */
  private async executeAction(pid: number, step: ActionStep): Promise<ActionResult> {
    const elements = await this.uab.query(pid, step.selector);
    if (elements.length === 0) {
      throw new Error(
        `No element found matching selector: ${JSON.stringify(step.selector)}`
      );
    }
    const target = elements[0];
    return this.uab.act(pid, target.id, step.action, step.params);
  }

  /** Wait for an element to appear (or disappear) */
  private async executeWait(pid: number, step: WaitStep): Promise<void> {
    const timeout = step.timeoutMs ?? 10_000;
    const poll = step.pollMs ?? 500;
    const deadline = Date.now() + timeout;

    while (Date.now() < deadline) {
      const elements = await this.uab.query(pid, step.selector);
      const found = elements.length > 0;

      if (step.waitForAbsence ? !found : found) {
        return; // Condition met
      }

      await new Promise(r => setTimeout(r, poll));
    }

    const condition = step.waitForAbsence ? 'disappear' : 'appear';
    throw new Error(
      `Timeout waiting for element to ${condition}: ${JSON.stringify(step.selector)} (${timeout}ms)`
    );
  }

  /** Execute conditional step — check element, branch accordingly */
  private async executeConditional(pid: number, step: ConditionalStep): Promise<void> {
    const elements = await this.uab.query(pid, step.selector);
    const present = elements.length > 0;

    const branch = present ? step.ifPresent : (step.ifAbsent || []);
    for (const subStep of branch) {
      await this.executeStep(pid, subStep);
    }
  }

  /** Type text into an element, optionally clearing first */
  private async executeTypeText(pid: number, step: TypeTextStep): Promise<ActionResult> {
    const elements = await this.uab.query(pid, step.selector);
    if (elements.length === 0) {
      throw new Error(
        `No element found for typing: ${JSON.stringify(step.selector)}`
      );
    }
    const target = elements[0];

    if (step.clearFirst) {
      await this.uab.act(pid, target.id, 'clear');
      await new Promise(r => setTimeout(r, 100));
    }

    return this.uab.act(pid, target.id, 'type', { text: step.text });
  }
}

// ─── Pre-built Chain Templates ─────────────────────────────────

/** Create a "fill form" chain from field/value pairs */
export function buildFormChain(
  pid: number,
  name: string,
  fields: Array<{ selector: ElementSelector; value: string; clearFirst?: boolean }>,
  submitSelector?: ElementSelector,
): ChainDefinition {
  const steps: ChainStep[] = fields.map(f => ({
    type: 'typeText' as const,
    selector: f.selector,
    text: f.value,
    clearFirst: f.clearFirst ?? true,
    label: `Fill "${f.selector.label || f.selector.type || 'field'}"`,
  }));

  if (submitSelector) {
    steps.push({
      type: 'action' as const,
      selector: submitSelector,
      action: 'click',
      label: 'Submit form',
    });
  }

  return { name, pid, steps };
}

/** Create a "navigate menu" chain (click through menu items) */
export function buildMenuChain(
  pid: number,
  name: string,
  menuPath: string[], // e.g., ['File', 'Save As...']
): ChainDefinition {
  const steps: ChainStep[] = menuPath.map((label, i) => ({
    type: 'action' as const,
    selector: { type: i === 0 ? 'menu' as const : 'menuitem' as const, label },
    action: 'click' as const,
    label: `Click "${label}"`,
  }));

  return { name, pid, steps, stepDelay: 300 };
}
