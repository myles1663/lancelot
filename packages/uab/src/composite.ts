/**
 * UAB Composite Query Engine
 *
 * The fastest, most capable method of computer use.
 *
 * Combines ALL available data sources in speed-priority order:
 *   1. UIA Tree     (⚡ direct) — element IDs, types, states, structure
 *   2. Bounding Rects (⚡ direct) — spatial positions, sizes → spatial map
 *   3. Text Reading  (⚡ fast) — TextPattern/ValuePattern content extraction
 *   4. Vision        (🐌 slow) — screenshot + Claude Vision (ONLY when needed)
 *
 * Philosophy:
 *   - Data is FASTER than images for AI to process
 *   - The spatial map REPLACES screenshots in most cases
 *   - Vision is complementary (verification, complex visuals), not primary
 *   - Every extra tool closes another gap — use ALL of them
 *
 * SPDX-License-Identifier: BUSL-1.1
 * Licensor: Myles Russell Hamilton
 */

import type { UIElement, Bounds, ActionType, ActionResult, AppState } from './types.js';
import { buildSpatialMap, SpatialIndex, renderTextMap, renderJsonMap } from './spatial.js';
import type { SpatialMap, SpatialElement } from './spatial.js';

// ─── UAB-like interface ─────────────────────────────────────────
// Both UABService and UABConnector implement these methods,
// so CompositeEngine works with either.

export interface UABLike {
  enumerate(pid: number, maxDepth?: number): Promise<UIElement[]>;
  state(pid: number): Promise<AppState>;
  act(pid: number, elementId: string, action: ActionType, params?: Record<string, unknown>): Promise<ActionResult>;
  screenshot(pid: number, outputPath?: string): Promise<ActionResult>;
}

// ─── Helpers ───────────────────────────────────────────────────

/** Derive window bounds from element bounding rects when state() returns zeros. */
function deriveWindowBounds(tree: UIElement[]): Bounds {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

  function walk(elements: UIElement[]) {
    for (const el of elements) {
      if (el.bounds.width > 0 && el.bounds.height > 0) {
        minX = Math.min(minX, el.bounds.x);
        minY = Math.min(minY, el.bounds.y);
        maxX = Math.max(maxX, el.bounds.x + el.bounds.width);
        maxY = Math.max(maxY, el.bounds.y + el.bounds.height);
      }
      walk(el.children);
    }
  }
  walk(tree);

  if (minX === Infinity) return { x: 0, y: 0, width: 0, height: 0 };
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

// ─── Types ─────────────────────────────────────────────────────

export interface CompositeResult {
  pid: number;
  /** The spatial map (rows, grid, indexed) */
  spatialMap: SpatialMap;
  /** Spatial index for fast queries */
  index: SpatialIndex;
  /** Text content extracted from elements (id → text) */
  textContent: Map<string, string>;
  /** Window state */
  appState: AppState;
  /** Performance timing */
  timing: {
    enumerateMs: number;
    spatialBuildMs: number;
    textReadMs: number;
    totalMs: number;
    visionMs?: number;
  };
  /** How many elements have text content */
  textEnrichedCount: number;
}

export interface CompositeOptions {
  /** Max tree depth for enumeration (default: 12) */
  maxDepth?: number;
  /** Row clustering threshold in pixels (default: 15) */
  rowThreshold?: number;
  /** Read text content from elements (default: true) */
  readText?: boolean;
  /** Max elements to attempt text reading on (default: 200) */
  textReadLimit?: number;
  /** Element types to read text from (default: text-bearing types) */
  textTypes?: string[];
  /** Also capture a screenshot for vision verification (default: false) */
  includeVision?: boolean;
  /** Output format for text map (default: 'detailed') */
  mapFormat?: 'detailed' | 'compact' | 'json';
}

const TEXT_BEARING_TYPES = new Set([
  'textfield', 'textarea', 'label', 'heading', 'link',
  'button', 'menuitem', 'listitem', 'treeitem', 'tab',
  'tablecell', 'tooltip', 'statusbar',
]);

// ─── Composite Engine ──────────────────────────────────────────

export class CompositeEngine {
  private uab: UABLike;

  constructor(uab: UABLike) {
    this.uab = uab;
  }

  /**
   * Run a full composite query on a connected app.
   * Returns spatial map + text content + app state in one call.
   */
  async query(pid: number, options?: CompositeOptions): Promise<CompositeResult> {
    const opts = {
      maxDepth: options?.maxDepth ?? 12,
      rowThreshold: options?.rowThreshold ?? 15,
      readText: options?.readText ?? true,
      textReadLimit: options?.textReadLimit ?? 200,
      textTypes: options?.textTypes ?? Array.from(TEXT_BEARING_TYPES),
      includeVision: options?.includeVision ?? false,
    };

    const totalStart = Date.now();
    const textContent = new Map<string, string>();

    // Step 1: Enumerate UI tree + get app state (parallel)
    const enumStart = Date.now();
    const [tree, appState] = await Promise.all([
      this.uab.enumerate(pid, opts.maxDepth),
      this.uab.state(pid),
    ]);
    const enumerateMs = Date.now() - enumStart;

    // Step 2: Build spatial map from bounding rects
    const spatialStart = Date.now();
    let windowBounds: Bounds = {
      x: appState.window.position.x,
      y: appState.window.position.y,
      width: appState.window.size.width,
      height: appState.window.size.height,
    };

    // If state returned zero bounds, derive from element bounding rects
    if (windowBounds.width === 0 || windowBounds.height === 0) {
      windowBounds = deriveWindowBounds(tree);
    }

    const spatialMap = buildSpatialMap(pid, tree, windowBounds, {
      maxDepth: opts.maxDepth,
      rowThreshold: opts.rowThreshold,
    });
    const index = new SpatialIndex(spatialMap);
    const spatialBuildMs = Date.now() - spatialStart;

    // Step 3: Text reading enrichment
    let textReadMs = 0;
    if (opts.readText) {
      const textStart = Date.now();
      await this.enrichWithText(pid, index, textContent, opts);
      textReadMs = Date.now() - textStart;
    }

    // Step 4: Vision (optional, only if requested)
    let visionMs: number | undefined;
    if (opts.includeVision) {
      const visionStart = Date.now();
      try {
        await this.uab.screenshot(pid);
      } catch {
        // Vision is optional — don't fail the whole query
      }
      visionMs = Date.now() - visionStart;
    }

    return {
      pid,
      spatialMap,
      index,
      textContent,
      appState,
      timing: {
        enumerateMs,
        spatialBuildMs,
        textReadMs,
        totalMs: Date.now() - totalStart,
        visionMs,
      },
      textEnrichedCount: textContent.size,
    };
  }

  /**
   * Quick spatial map — skip text reading for maximum speed.
   * Use when you just need positions, not content.
   */
  async quickMap(pid: number): Promise<{ map: SpatialMap; index: SpatialIndex; timing: number }> {
    const start = Date.now();

    const [tree, appState] = await Promise.all([
      this.uab.enumerate(pid),
      this.uab.state(pid),
    ]);

    const windowBounds: Bounds = {
      x: appState.window.position.x,
      y: appState.window.position.y,
      width: appState.window.size.width,
      height: appState.window.size.height,
    };

    const map = buildSpatialMap(pid, tree, windowBounds);
    const index = new SpatialIndex(map);

    return { map, index, timing: Date.now() - start };
  }

  /**
   * Generate a text map string for AI consumption.
   * This is the primary output — replaces screenshots.
   */
  async textMap(pid: number, options?: CompositeOptions & {
    format?: 'detailed' | 'compact' | 'json';
  }): Promise<{ text: string; timing: number }> {
    const result = await this.query(pid, options);
    const format = options?.format ?? 'detailed';

    let text: string;
    if (format === 'json') {
      text = JSON.stringify(renderJsonMap(result.spatialMap), null, 2);
    } else {
      text = renderTextMap(result.spatialMap, {
        showBounds: true,
        showContent: true,
        compact: format === 'compact',
      });
    }

    // Append timing info
    const t = result.timing;
    text += `\n\n--- Timing: enumerate=${t.enumerateMs}ms, spatial=${t.spatialBuildMs}ms, text=${t.textReadMs}ms, total=${t.totalMs}ms ---`;

    return { text, timing: result.timing.totalMs };
  }

  /**
   * Find an element by description using the spatial map.
   * Faster than screenshot-based element finding.
   */
  async findElement(pid: number, description: string): Promise<SpatialElement[]> {
    const result = await this.query(pid, { readText: true });

    // Try exact label match first
    const exactMatch = result.index.query({ label: description });
    if (exactMatch.length > 0) return exactMatch;

    // Try matching against text content
    const lower = description.toLowerCase();
    const textMatches = result.index.all().filter(el => {
      const text = result.textContent.get(el.id);
      if (text && text.toLowerCase().includes(lower)) return true;
      if (el.label.toLowerCase().includes(lower)) return true;
      return false;
    });

    return textMatches;
  }

  /**
   * Click the nearest matching element.
   * Spatial-map-first approach (no screenshots needed).
   */
  async clickElement(pid: number, description: string): Promise<ActionResult> {
    const matches = await this.findElement(pid, description);
    if (matches.length === 0) {
      return { success: false, error: `No element found matching "${description}"` };
    }

    // Pick the best match (first = closest label match)
    const target = matches[0];
    return this.uab.act(pid, target.id, 'click');
  }

  /**
   * Type into the nearest matching text field.
   */
  async typeInto(pid: number, fieldDescription: string, text: string): Promise<ActionResult> {
    const matches = await this.findElement(pid, fieldDescription);
    const textFields = matches.filter(m => m.type === 'textfield' || m.type === 'textarea');
    const target = textFields.length > 0 ? textFields[0] : matches[0];

    if (!target) {
      return { success: false, error: `No text field found matching "${fieldDescription}"` };
    }

    // Click to focus, then type
    await this.uab.act(pid, target.id, 'click');
    return this.uab.act(pid, target.id, 'type', { text });
  }

  // ─── Private Helpers ─────────────────────────────────────────

  /**
   * Enrich spatial elements with text content.
   *
   * Strategy (speed-first):
   * 1. Labels ARE text — most elements already have their text in el.label (FREE)
   * 2. Only call readDocument on textareas/textfields — these are the ones
   *    where the label says "Text editor" but the actual content is different
   * 3. This keeps text enrichment under 1-2 seconds instead of 80+
   */
  private async enrichWithText(
    pid: number,
    index: SpatialIndex,
    textContent: Map<string, string>,
    opts: { textTypes: string[]; textReadLimit: number },
  ): Promise<void> {
    // Step 1: Labels as text (direct — no API calls)
    for (const el of index.all()) {
      if (el.label && el.label.length > 0) {
        textContent.set(el.id, el.label);
        el.text = el.label;
      }
    }

    // Step 2: Only fetch actual text content for text input areas
    // These are elements where the label is just a name ("Text editor")
    // but the real content is what the user typed
    const deepReadTypes = new Set(['textarea', 'textfield']);
    const candidates = index.all()
      .filter(el => deepReadTypes.has(el.type) && el.visible)
      .slice(0, 10); // Very limited — these are slow

    if (candidates.length === 0) return;

    const results = await Promise.allSettled(
      candidates.map(el =>
        this.uab.act(pid, el.id, 'readDocument' as ActionType)
          .then(r => ({ id: el.id, text: r.success ? String(r.result || '') : '' }))
          .catch(() => ({ id: el.id, text: '' }))
      ),
    );

    for (const r of results) {
      if (r.status === 'fulfilled' && r.value.text) {
        textContent.set(r.value.id, r.value.text);
        const el = index.all().find(e => e.id === r.value.id);
        if (el) el.text = r.value.text;
      }
    }
  }
}
