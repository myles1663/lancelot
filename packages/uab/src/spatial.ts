/**
 * UAB Spatial Map Engine
 *
 * Converts flat UIElement[] with bounding rects into a spatial index
 * that enables fast positional queries, row/column detection, and
 * generates compact text-based maps for AI consumption.
 *
 * This is the CORE of UAB's speed advantage over vision-only approaches:
 * - Data > screenshots (AI processes structured data faster than images)
 * - Bounding rects are FREE from UIA (no extra API calls)
 * - Spatial map eliminates the need for screenshots in most cases
 * - Vision becomes complementary, not primary
 *
 * SPDX-License-Identifier: BSL-1.0
 * Licensor: Myles Russell Hamilton
 */

import type { UIElement, Bounds, ElementType } from './types.js';

// ─── Types ─────────────────────────────────────────────────────

export interface SpatialElement {
  id: string;
  type: ElementType;
  label: string;
  bounds: Bounds;
  center: { x: number; y: number };
  actions: string[];
  visible: boolean;
  enabled: boolean;
  text?: string;         // From ReadText enrichment
  value?: string;        // From ValuePattern enrichment
  row?: number;          // Detected visual row index
  col?: number;          // Detected visual column index
}

export interface SpatialRow {
  index: number;
  y: number;             // Top Y of this row band
  height: number;        // Height of this row band
  elements: SpatialElement[];
}

export interface SpatialMap {
  pid: number;
  windowBounds: Bounds;
  totalElements: number;
  rows: SpatialRow[];
  grid: SpatialElement[][]; // [row][col] - elements sorted left-to-right in each row
  timestamp: number;
}

export interface SpatialQuery {
  /** Find elements near a point */
  nearPoint?: { x: number; y: number; radius?: number };
  /** Find elements in a rectangular region */
  inRegion?: Bounds;
  /** Find elements in a specific row */
  row?: number;
  /** Find elements by type */
  type?: ElementType;
  /** Find elements by label (substring match) */
  label?: string;
  /** Limit results */
  limit?: number;
}

export interface NearestResult {
  element: SpatialElement;
  distance: number;
  direction: 'above' | 'below' | 'left' | 'right' | 'overlapping';
}

// ─── Grid Cell Index (for fast spatial lookups) ────────────────

const CELL_SIZE = 50; // pixels per grid cell

interface GridIndex {
  cells: Map<string, SpatialElement[]>;
  cellSize: number;
}

function cellKey(cx: number, cy: number): string {
  return `${cx},${cy}`;
}

function buildGridIndex(elements: SpatialElement[]): GridIndex {
  const cells = new Map<string, SpatialElement[]>();
  const cellSize = CELL_SIZE;

  for (const el of elements) {
    const b = el.bounds;
    const minCX = Math.floor(b.x / cellSize);
    const minCY = Math.floor(b.y / cellSize);
    const maxCX = Math.floor((b.x + b.width) / cellSize);
    const maxCY = Math.floor((b.y + b.height) / cellSize);

    for (let cx = minCX; cx <= maxCX; cx++) {
      for (let cy = minCY; cy <= maxCY; cy++) {
        const key = cellKey(cx, cy);
        const bucket = cells.get(key);
        if (bucket) {
          bucket.push(el);
        } else {
          cells.set(key, [el]);
        }
      }
    }
  }

  return { cells, cellSize };
}

// ─── Row Detection ─────────────────────────────────────────────

/**
 * Cluster elements into visual rows by Y-coordinate proximity.
 * Elements whose vertical centers are within `threshold` pixels
 * of each other are grouped into the same row.
 */
function detectRows(elements: SpatialElement[], threshold = 15): SpatialRow[] {
  if (elements.length === 0) return [];

  // Sort by center Y
  const sorted = [...elements].sort((a, b) => a.center.y - b.center.y);

  const rows: SpatialRow[] = [];
  let currentRow: SpatialElement[] = [sorted[0]];
  let rowCenterY = sorted[0].center.y;

  for (let i = 1; i < sorted.length; i++) {
    const el = sorted[i];
    // If this element's center Y is close to the current row's average, add to row
    if (Math.abs(el.center.y - rowCenterY) <= threshold) {
      currentRow.push(el);
      // Update running average
      rowCenterY = currentRow.reduce((sum, e) => sum + e.center.y, 0) / currentRow.length;
    } else {
      // Finalize current row, start new one
      rows.push(finalizeRow(rows.length, currentRow));
      currentRow = [el];
      rowCenterY = el.center.y;
    }
  }
  // Don't forget the last row
  if (currentRow.length > 0) {
    rows.push(finalizeRow(rows.length, currentRow));
  }

  // Assign row/col indices to elements
  for (const row of rows) {
    row.elements.forEach((el, colIdx) => {
      el.row = row.index;
      el.col = colIdx;
    });
  }

  return rows;
}

function finalizeRow(index: number, elements: SpatialElement[]): SpatialRow {
  // Sort elements left-to-right within the row
  elements.sort((a, b) => a.center.x - b.center.x);

  const minY = Math.min(...elements.map(e => e.bounds.y));
  const maxY = Math.max(...elements.map(e => e.bounds.y + e.bounds.height));

  return {
    index,
    y: minY,
    height: maxY - minY,
    elements,
  };
}

// ─── Core Spatial Map Builder ──────────────────────────────────

/**
 * Flatten a UIElement tree into a list of SpatialElements.
 * Filters out invisible (zero-size) elements.
 */
function flattenElements(tree: UIElement[], maxDepth = 12, depth = 0): SpatialElement[] {
  const result: SpatialElement[] = [];
  if (depth > maxDepth) return result;

  for (const el of tree) {
    // Skip invisible elements (zero bounds)
    if (el.bounds.width > 0 && el.bounds.height > 0 && el.visible) {
      result.push({
        id: el.id,
        type: el.type,
        label: el.label || '',
        bounds: el.bounds,
        center: {
          x: Math.round(el.bounds.x + el.bounds.width / 2),
          y: Math.round(el.bounds.y + el.bounds.height / 2),
        },
        actions: el.actions as string[],
        visible: el.visible,
        enabled: el.enabled,
      });
    }
    // Recurse into children
    result.push(...flattenElements(el.children, maxDepth, depth + 1));
  }

  return result;
}

/**
 * Build a complete spatial map from a UI element tree.
 */
export function buildSpatialMap(
  pid: number,
  tree: UIElement[],
  windowBounds: Bounds,
  options?: { maxDepth?: number; rowThreshold?: number },
): SpatialMap {
  const maxDepth = options?.maxDepth ?? 12;
  const rowThreshold = options?.rowThreshold ?? 15;

  // Flatten tree to spatial elements
  const rawElements = flattenElements(tree, maxDepth);

  // Deduplicate: remove elements with identical type+label+bounds
  const elements = deduplicateElements(rawElements);

  // Detect rows
  const rows = detectRows(elements, rowThreshold);

  // Build grid (rows × cols)
  const grid = rows.map(r => r.elements);

  return {
    pid,
    windowBounds,
    totalElements: elements.length,
    rows,
    grid,
    timestamp: Date.now(),
  };
}

// ─── Spatial Queries ───────────────────────────────────────────

export class SpatialIndex {
  private elements: SpatialElement[];
  private gridIndex: GridIndex;
  private map: SpatialMap;

  constructor(map: SpatialMap) {
    this.map = map;
    this.elements = map.rows.flatMap(r => r.elements);
    this.gridIndex = buildGridIndex(this.elements);
  }

  /** Find elements near a point (fast grid-based lookup). */
  nearPoint(x: number, y: number, radius = 100): NearestResult[] {
    const results: NearestResult[] = [];
    const cs = this.gridIndex.cellSize;

    // Check cells in the radius
    const minCX = Math.floor((x - radius) / cs);
    const maxCX = Math.floor((x + radius) / cs);
    const minCY = Math.floor((y - radius) / cs);
    const maxCY = Math.floor((y + radius) / cs);

    const seen = new Set<string>();

    for (let cx = minCX; cx <= maxCX; cx++) {
      for (let cy = minCY; cy <= maxCY; cy++) {
        const bucket = this.gridIndex.cells.get(cellKey(cx, cy));
        if (!bucket) continue;
        for (const el of bucket) {
          if (seen.has(el.id)) continue;
          seen.add(el.id);

          const dist = distanceToRect(x, y, el.bounds);
          if (dist <= radius) {
            results.push({
              element: el,
              distance: dist,
              direction: getDirection(x, y, el.bounds),
            });
          }
        }
      }
    }

    return results.sort((a, b) => a.distance - b.distance);
  }

  /** Find the single nearest element to a point. */
  nearest(x: number, y: number): NearestResult | null {
    let best: NearestResult | null = null;

    for (const el of this.elements) {
      const dist = distanceToRect(x, y, el.bounds);
      if (!best || dist < best.distance) {
        best = {
          element: el,
          distance: dist,
          direction: getDirection(x, y, el.bounds),
        };
      }
      if (dist === 0) break; // Can't get closer than overlapping
    }

    return best;
  }

  /** Find all elements within a rectangular region. */
  inRegion(region: Bounds): SpatialElement[] {
    const results: SpatialElement[] = [];
    const cs = this.gridIndex.cellSize;

    const minCX = Math.floor(region.x / cs);
    const maxCX = Math.floor((region.x + region.width) / cs);
    const minCY = Math.floor(region.y / cs);
    const maxCY = Math.floor((region.y + region.height) / cs);

    const seen = new Set<string>();

    for (let cx = minCX; cx <= maxCX; cx++) {
      for (let cy = minCY; cy <= maxCY; cy++) {
        const bucket = this.gridIndex.cells.get(cellKey(cx, cy));
        if (!bucket) continue;
        for (const el of bucket) {
          if (seen.has(el.id)) continue;
          seen.add(el.id);
          if (rectsOverlap(el.bounds, region)) {
            results.push(el);
          }
        }
      }
    }

    return results;
  }

  /** Get elements from a specific visual row. */
  row(index: number): SpatialElement[] {
    return this.map.rows[index]?.elements ?? [];
  }

  /** Run a compound spatial query. */
  query(q: SpatialQuery): SpatialElement[] {
    let results: SpatialElement[];

    if (q.nearPoint) {
      results = this.nearPoint(q.nearPoint.x, q.nearPoint.y, q.nearPoint.radius)
        .map(r => r.element);
    } else if (q.inRegion) {
      results = this.inRegion(q.inRegion);
    } else if (q.row !== undefined) {
      results = this.row(q.row);
    } else {
      results = this.elements;
    }

    // Apply filters
    if (q.type) results = results.filter(e => e.type === q.type);
    if (q.label) {
      const lower = q.label.toLowerCase();
      results = results.filter(e => e.label.toLowerCase().includes(lower));
    }
    if (q.limit) results = results.slice(0, q.limit);

    return results;
  }

  /** Get all elements. */
  all(): SpatialElement[] {
    return this.elements;
  }

  /** Get the underlying map. */
  getMap(): SpatialMap {
    return this.map;
  }

  /** How many elements in the index. */
  get size(): number {
    return this.elements.length;
  }
}

// ─── Text Map Renderer ─────────────────────────────────────────

/**
 * Generate a compact text-based representation of the spatial map.
 * This is what gets sent to the AI instead of a screenshot.
 */
export function renderTextMap(map: SpatialMap, options?: {
  /** Include bounds coordinates (default: true) */
  showBounds?: boolean;
  /** Include action list (default: false — too verbose) */
  showActions?: boolean;
  /** Max elements per row to show (default: 20) */
  maxPerRow?: number;
  /** Include text/value content (default: true) */
  showContent?: boolean;
  /** Compact single-line format (default: false) */
  compact?: boolean;
}): string {
  const showBounds = options?.showBounds ?? true;
  const showActions = options?.showActions ?? false;
  const maxPerRow = options?.maxPerRow ?? 20;
  const showContent = options?.showContent ?? true;
  const compact = options?.compact ?? false;

  const lines: string[] = [];
  const wb = map.windowBounds;

  lines.push(`=== SPATIAL MAP (PID ${map.pid}) — ${map.totalElements} elements, ${map.rows.length} rows ===`);
  lines.push(`Window: ${wb.width}×${wb.height} at (${wb.x},${wb.y})`);

  if (compact) {
    // Single-line per element, tab-separated
    lines.push('');
    lines.push('ROW | TYPE | LABEL | BOUNDS | ID');
    lines.push('---|---|---|---|---');
    for (const row of map.rows) {
      for (const el of row.elements.slice(0, maxPerRow)) {
        const bounds = `${el.bounds.x},${el.bounds.y} ${el.bounds.width}×${el.bounds.height}`;
        const content = (showContent && (el.text || el.value))
          ? ` "${el.text || el.value}"`
          : '';
        lines.push(`${row.index} | ${el.type} | ${truncate(el.label, 40)}${content} | ${bounds} | ${el.id}`);
      }
    }
  } else {
    for (const row of map.rows) {
      lines.push('');

      // Row header with element type summary
      const typeCounts = new Map<string, number>();
      for (const el of row.elements) {
        typeCounts.set(el.type, (typeCounts.get(el.type) || 0) + 1);
      }
      const typeSummary = Array.from(typeCounts.entries())
        .map(([type, count]) => count > 1 ? `${count}×${type}` : type)
        .join(', ');

      lines.push(`ROW ${row.index} (y:${row.y}-${row.y + row.height}) — ${typeSummary}`);

      // Elements in this row
      const shown = row.elements.slice(0, maxPerRow);
      for (const el of shown) {
        let entry = `  [${el.type}`;
        if (el.label) entry += ` "${truncate(el.label, 50)}"`;
        if (showContent && el.text && el.text !== el.label) {
          entry += ` text="${truncate(el.text, 30)}"`;
        }
        if (showContent && el.value) {
          entry += ` val="${truncate(el.value, 30)}"`;
        }
        if (showBounds) {
          entry += ` @(${el.bounds.x},${el.bounds.y} ${el.bounds.width}×${el.bounds.height})`;
        }
        if (!el.enabled) entry += ' DISABLED';
        if (showActions && el.actions.length > 0) {
          entry += ` actions=${el.actions.join(',')}`;
        }
        entry += ` id=${el.id}]`;
        lines.push(entry);
      }

      if (row.elements.length > maxPerRow) {
        lines.push(`  ... +${row.elements.length - maxPerRow} more`);
      }
    }
  }

  return lines.join('\n');
}

/**
 * Generate a JSON-optimized map output for CLI/API consumption.
 * Includes all data but in a flat, easily-parseable structure.
 */
export function renderJsonMap(map: SpatialMap): object {
  return {
    pid: map.pid,
    window: map.windowBounds,
    totalElements: map.totalElements,
    rowCount: map.rows.length,
    timestamp: map.timestamp,
    rows: map.rows.map(row => ({
      index: row.index,
      y: row.y,
      height: row.height,
      elementCount: row.elements.length,
      elements: row.elements.map(el => ({
        id: el.id,
        type: el.type,
        label: el.label || undefined,
        text: el.text || undefined,
        value: el.value || undefined,
        bounds: el.bounds,
        center: el.center,
        enabled: el.enabled,
        actions: el.actions,
        row: el.row,
        col: el.col,
      })),
    })),
  };
}

// ─── Helpers ───────────────────────────────────────────────────

/** Remove duplicate elements that have identical type+label+bounds. */
function deduplicateElements(elements: SpatialElement[]): SpatialElement[] {
  const seen = new Set<string>();
  const result: SpatialElement[] = [];
  for (const el of elements) {
    const key = `${el.type}|${el.label}|${el.bounds.x},${el.bounds.y},${el.bounds.width},${el.bounds.height}`;
    if (!seen.has(key)) {
      seen.add(key);
      result.push(el);
    }
  }
  return result;
}

/** Distance from a point to the nearest edge of a rectangle. */
function distanceToRect(px: number, py: number, rect: Bounds): number {
  const cx = Math.max(rect.x, Math.min(px, rect.x + rect.width));
  const cy = Math.max(rect.y, Math.min(py, rect.y + rect.height));
  const dx = px - cx;
  const dy = py - cy;
  return Math.sqrt(dx * dx + dy * dy);
}

/** Determine the direction from a point to a rectangle. */
function getDirection(px: number, py: number, rect: Bounds): NearestResult['direction'] {
  const rcx = rect.x + rect.width / 2;
  const rcy = rect.y + rect.height / 2;

  // Check if point is inside the rect
  if (px >= rect.x && px <= rect.x + rect.width &&
      py >= rect.y && py <= rect.y + rect.height) {
    return 'overlapping';
  }

  const dx = px - rcx;
  const dy = py - rcy;

  if (Math.abs(dx) > Math.abs(dy)) {
    return dx > 0 ? 'left' : 'right';
  } else {
    return dy > 0 ? 'above' : 'below';
  }
}

/** Check if two rectangles overlap. */
function rectsOverlap(a: Bounds, b: Bounds): boolean {
  return !(a.x + a.width < b.x || b.x + b.width < a.x ||
           a.y + a.height < b.y || b.y + b.height < a.y);
}

/** Truncate a string with ellipsis. */
function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + '…';
}
