/**
 * Vision Analyzer — Screenshot → UIElement[] via Claude Vision API
 *
 * Takes a screenshot image and sends it to Claude's vision model
 * to identify all visible UI elements with their bounding boxes.
 * This is the "eyes" of the Vision fallback — expensive but universal.
 *
 * Uses Claude claude-sonnet-4-20250514 for cost efficiency (vision analysis
 * doesn't need Opus-level reasoning).
 */

import type { UIElement, ElementType, ActionType, Bounds } from '../../types.js';

// ─── Configuration ───────────────────────────────────────────

export interface VisionAnalyzerOptions {
  /** Anthropic API key. Falls back to ANTHROPIC_API_KEY env var. */
  apiKey?: string;
  /** Model to use for vision analysis. Default: claude-sonnet-4-20250514 */
  model?: string;
  /** Max tokens for response. Default: 4096 */
  maxTokens?: number;
}

// ─── Analysis Result ─────────────────────────────────────────

interface RawVisionElement {
  type: string;
  label: string;
  bounds: { x: number; y: number; width: number; height: number };
  enabled?: boolean;
  visible?: boolean;
}

// ─── Analyzer ────────────────────────────────────────────────

const ANALYSIS_PROMPT = `You are a UI element analyzer for desktop application automation. Analyze this screenshot and identify ALL visible interactive UI elements.

For EACH element, provide:
- type: One of: button, textfield, textarea, checkbox, radio, select, menu, menuitem, list, listitem, tab, link, label, heading, image, slider, progressbar, toolbar, statusbar, dialog, container, unknown
- label: The visible text or description of the element
- bounds: Pixel coordinates as {x, y, width, height} relative to the image top-left corner
- enabled: Whether the element appears interactive (not grayed out)

Return ONLY a JSON array. No explanation, no markdown fences. Example:
[{"type":"button","label":"OK","bounds":{"x":100,"y":200,"width":80,"height":30},"enabled":true}]

Focus on interactive elements (buttons, inputs, links, tabs, menus) first. Include labels and headings for context. Be precise with bounding box coordinates — they will be used for mouse clicks.`;

export class VisionAnalyzer {
  private client: unknown = null;
  private model: string;
  private maxTokens: number;
  private apiKey: string | undefined;

  constructor(options?: VisionAnalyzerOptions) {
    this.apiKey = options?.apiKey || process.env.ANTHROPIC_API_KEY;
    this.model = options?.model || 'claude-sonnet-4-20250514';
    this.maxTokens = options?.maxTokens || 4096;
  }

  /**
   * Check if the analyzer is configured (has API key).
   */
  get available(): boolean {
    return !!(this.apiKey || process.env.ANTHROPIC_API_KEY);
  }

  /**
   * Analyze a screenshot and return identified UI elements.
   *
   * @param base64Image - PNG image as base64 string
   * @param windowBounds - The absolute screen position of the window
   *                       (used to convert relative coords to absolute)
   */
  async analyze(
    base64Image: string,
    windowBounds: { x: number; y: number; width: number; height: number },
  ): Promise<UIElement[]> {
    if (!this.available) {
      throw new Error(
        'Vision analyzer requires ANTHROPIC_API_KEY. ' +
        'Set it in .env or pass apiKey to VisionAnalyzerOptions.'
      );
    }

    // Lazy-init client (dynamic import to avoid hard dependency on @anthropic-ai/sdk)
    if (!this.client) {
      try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const sdkName = '@anthropic-ai/sdk';
        const { default: Anthropic } = await (Function('specifier', 'return import(specifier)')(sdkName) as Promise<{ default: new (opts: { apiKey?: string }) => unknown }>);
        this.client = new Anthropic({ apiKey: this.apiKey });
      } catch {
        throw new Error(
          'Vision plugin requires @anthropic-ai/sdk. Install it: npm install @anthropic-ai/sdk'
        );
      }
    }

    const client = this.client as { messages: { create: (params: unknown) => Promise<{ content: Array<{ type: string; text?: string }> }> } };
    const response = await client.messages.create({
      model: this.model,
      max_tokens: this.maxTokens,
      messages: [
        {
          role: 'user',
          content: [
            {
              type: 'image',
              source: {
                type: 'base64',
                media_type: 'image/png',
                data: base64Image,
              },
            },
            {
              type: 'text',
              text: ANALYSIS_PROMPT,
            },
          ],
        },
      ],
    });

    // Extract JSON from response
    const textBlock = response.content.find((b: { type: string }) => b.type === 'text');
    if (!textBlock || textBlock.type !== 'text') {
      throw new Error('Vision API returned no text response');
    }

    const rawElements = this.parseResponse((textBlock as { text: string }).text);
    return this.mapToUIElements(rawElements, windowBounds);
  }

  /**
   * Parse the Claude response into raw element objects.
   */
  private parseResponse(text: string): RawVisionElement[] {
    // Strip markdown fences if present
    let cleaned = text.trim();
    if (cleaned.startsWith('```')) {
      cleaned = cleaned.replace(/^```(?:json)?\s*/, '').replace(/\s*```$/, '');
    }

    // Find the JSON array
    const start = cleaned.indexOf('[');
    const end = cleaned.lastIndexOf(']');
    if (start === -1 || end === -1) {
      throw new Error('Vision API response does not contain a JSON array');
    }

    try {
      return JSON.parse(cleaned.substring(start, end + 1));
    } catch (err) {
      throw new Error(`Failed to parse vision response: ${err instanceof Error ? err.message : err}`);
    }
  }

  /**
   * Convert raw vision elements to UAB UIElement format.
   * Adds absolute screen coordinates and generates IDs.
   */
  private mapToUIElements(
    raw: RawVisionElement[],
    windowBounds: { x: number; y: number; width: number; height: number },
  ): UIElement[] {
    return raw.map((el, index) => {
      // Convert relative-to-image coords to absolute screen coords
      const absoluteBounds: Bounds = {
        x: windowBounds.x + el.bounds.x,
        y: windowBounds.y + el.bounds.y,
        width: el.bounds.width,
        height: el.bounds.height,
      };

      const elementType = this.mapElementType(el.type);
      const actions = this.inferActions(elementType);

      return {
        id: `vision-${index}`,
        type: elementType,
        label: el.label || '',
        properties: {
          source: 'vision',
          confidence: 'visual',
        },
        bounds: absoluteBounds,
        children: [],
        actions,
        visible: el.visible !== false,
        enabled: el.enabled !== false,
        meta: {
          relativeBounds: el.bounds, // Keep original relative coords
          analysisIndex: index,
        },
      };
    });
  }

  /**
   * Map string type names to ElementType.
   */
  private mapElementType(raw: string): ElementType {
    const typeMap: Record<string, ElementType> = {
      button: 'button', btn: 'button',
      textfield: 'textfield', input: 'textfield', text_input: 'textfield',
      textarea: 'textarea', text_area: 'textarea',
      checkbox: 'checkbox', check: 'checkbox',
      radio: 'radio', radiobutton: 'radio',
      select: 'select', dropdown: 'select', combobox: 'select',
      menu: 'menu', menubar: 'menu',
      menuitem: 'menuitem', menu_item: 'menuitem',
      list: 'list', listbox: 'list',
      listitem: 'listitem', list_item: 'listitem',
      tab: 'tab', tabitem: 'tab',
      link: 'link', hyperlink: 'link', anchor: 'link',
      label: 'label', text: 'label', statictext: 'label',
      heading: 'heading', header: 'heading', title: 'heading',
      image: 'image', icon: 'image', img: 'image',
      slider: 'slider', range: 'slider',
      progressbar: 'progressbar', progress: 'progressbar',
      toolbar: 'toolbar', tool_bar: 'toolbar',
      statusbar: 'statusbar', status_bar: 'statusbar',
      dialog: 'dialog', modal: 'dialog', popup: 'dialog',
      container: 'container', panel: 'container', group: 'container',
      scrollbar: 'scrollbar', scroll: 'scrollbar',
      tree: 'tree', treeview: 'tree',
      treeitem: 'treeitem', tree_item: 'treeitem',
      table: 'table', grid: 'table',
      window: 'window',
      tooltip: 'tooltip',
    };

    return typeMap[raw.toLowerCase()] || 'unknown';
  }

  /**
   * Infer available actions based on element type.
   */
  private inferActions(type: ElementType): ActionType[] {
    const actionMap: Record<string, ActionType[]> = {
      button: ['click', 'doubleclick', 'rightclick', 'hover', 'focus'],
      textfield: ['click', 'type', 'clear', 'focus', 'select'],
      textarea: ['click', 'type', 'clear', 'focus', 'select', 'scroll'],
      checkbox: ['click', 'check', 'uncheck', 'toggle'],
      radio: ['click', 'select'],
      select: ['click', 'expand', 'collapse'],
      menu: ['click', 'expand'],
      menuitem: ['click', 'hover'],
      list: ['scroll', 'click'],
      listitem: ['click', 'doubleclick', 'select'],
      tab: ['click'],
      link: ['click', 'hover'],
      label: ['click'],
      heading: ['click'],
      image: ['click', 'rightclick'],
      slider: ['click', 'scroll'],
      toolbar: ['click'],
      dialog: ['click', 'close'],
      container: ['click', 'scroll'],
      tree: ['click', 'expand', 'collapse', 'scroll'],
      treeitem: ['click', 'expand', 'collapse', 'select'],
      table: ['click', 'scroll'],
    };

    return actionMap[type] || ['click'];
  }
}
