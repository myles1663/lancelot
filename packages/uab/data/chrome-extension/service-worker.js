/**
 * UAB Bridge Chrome Extension — Service Worker
 *
 * Connects to UAB's local WebSocket server and bridges
 * Chrome API calls for browser automation without requiring
 * Chrome to be relaunched with --remote-debugging-port.
 *
 * Protocol: JSON over WebSocket
 *   Server → Extension: { id, method, params }
 *   Extension → Server: { id, result } or { id, error }
 */

const WS_URL = 'ws://localhost:8787';
const RECONNECT_DELAY_MS = 3000;
const KEEPALIVE_INTERVAL_MS = 25000;

let ws = null;
let reconnectTimer = null;

// ─── WebSocket Connection ──────────────────────────────────────

function connect() {
  if (ws && ws.readyState === WebSocket.OPEN) return;

  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.log('[UAB] Connected to UAB server');
    clearTimeout(reconnectTimer);
    // Send hello
    ws.send(JSON.stringify({ type: 'hello', version: '1.0.0', browser: 'chrome' }));
    // Start keepalive alarm
    chrome.alarms.create('keepalive', { periodInMinutes: 0.4 }); // ~24s
  };

  ws.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      console.error('[UAB] Invalid JSON from server:', event.data);
      return;
    }

    // Welcome message from server
    if (msg.type === 'welcome') {
      console.log('[UAB] Server acknowledged connection');
      return;
    }

    // Ping/pong keepalive
    if (msg.type === 'ping') {
      ws.send(JSON.stringify({ type: 'pong' }));
      return;
    }

    // Command from server
    if (msg.id && msg.method) {
      try {
        const result = await handleCommand(msg.method, msg.params || {});
        ws.send(JSON.stringify({ id: msg.id, result }));
      } catch (err) {
        ws.send(JSON.stringify({ id: msg.id, error: err.message || String(err) }));
      }
    }
  };

  ws.onclose = () => {
    console.log('[UAB] Disconnected from UAB server');
    ws = null;
    chrome.alarms.clear('keepalive');
    scheduleReconnect();
  };

  ws.onerror = (err) => {
    console.error('[UAB] WebSocket error:', err);
    // onclose will fire after this
  };
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
}

// Keep service worker alive via alarm
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'keepalive') {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'heartbeat' }));
    } else {
      connect();
    }
  }
});

// ─── UAB Relay ────────────────────────────────────────────────
//
// Proxies requests from Co-work (or any Chrome messaging client)
// to UABServer at localhost:3100. The extension runs on the host
// machine, so it can reach localhost directly.
//
// Co-work → chrome.runtime.sendMessage → this handler → fetch(localhost:3100) → response
//

const UAB_BASE = 'http://localhost:3100';

/**
 * Relay a request to UABServer.
 * @param {string} endpoint - e.g., "/scan", "/connect", "/find"
 * @param {object} body - JSON body to POST
 * @param {string} [apiKey] - Optional API key
 * @returns {Promise<object>} JSON response from UABServer
 */
async function relayToUAB(endpoint, body = {}, apiKey) {
  const headers = { 'Content-Type': 'application/json' };
  if (apiKey) headers['X-API-Key'] = apiKey;

  const method = endpoint === '/health' ? 'GET' : 'POST';
  const opts = { method, headers };
  if (method === 'POST') opts.body = JSON.stringify(body);

  const resp = await fetch(`${UAB_BASE}${endpoint}`, opts);
  return await resp.json();
}

// Listen for messages from Co-work / other extensions / web pages
// Message format: { type: "uab", endpoint: "/scan", body: {...}, apiKey: "..." }
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'uab' && msg.endpoint) {
    relayToUAB(msg.endpoint, msg.body || {}, msg.apiKey)
      .then(result => sendResponse({ success: true, data: result }))
      .catch(err => sendResponse({ success: false, error: err.message || String(err) }));
    return true; // Keep channel open for async response
  }
});

// Also support external messages (from Co-work's extension ID or web pages)
chrome.runtime.onMessageExternal.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'uab' && msg.endpoint) {
    relayToUAB(msg.endpoint, msg.body || {}, msg.apiKey)
      .then(result => sendResponse({ success: true, data: result }))
      .catch(err => sendResponse({ success: false, error: err.message || String(err) }));
    return true;
  }
});

// ─── Command Router ──────────────────────────────────────────

async function handleCommand(method, params) {
  const [ns, cmd] = method.split('.');

  switch (ns) {
    case 'tabs':    return handleTabs(cmd, params);
    case 'cookies': return handleCookies(cmd, params);
    case 'dom':     return handleDom(cmd, params);
    case 'storage': return handleStorage(cmd, params);
    case 'exec':    return handleExec(cmd, params);
    case 'nav':     return handleNav(cmd, params);
    case 'capture': return handleCapture(cmd, params);
    case 'uab':     return handleUABRelay(cmd, params);
    default:
      throw new Error(`Unknown namespace: ${ns}`);
  }
}

// ─── UAB Relay via WS (for UABServer-originated commands) ─────

async function handleUABRelay(cmd, params) {
  // cmd is the UAB endpoint without the slash, e.g., "scan", "connect", "find"
  const endpoint = '/' + cmd;
  return relayToUAB(endpoint, params, params._apiKey);
}

// ─── Tab Management ────────────────────────────────────────────

async function handleTabs(cmd, params) {
  switch (cmd) {
    case 'list': {
      const tabs = await chrome.tabs.query({});
      return tabs.map(t => ({
        id: String(t.id),
        title: t.title,
        url: t.url,
        active: t.active,
        index: t.index,
        windowId: t.windowId,
        pinned: t.pinned,
        status: t.status,
      }));
    }
    case 'get': {
      const tab = await chrome.tabs.get(Number(params.tabId));
      return { id: String(tab.id), title: tab.title, url: tab.url, active: tab.active };
    }
    case 'create': {
      const tab = await chrome.tabs.create({ url: params.url || 'about:blank' });
      return { id: String(tab.id), title: tab.title, url: tab.url };
    }
    case 'close': {
      await chrome.tabs.remove(Number(params.tabId));
      return { success: true };
    }
    case 'activate': {
      await chrome.tabs.update(Number(params.tabId), { active: true });
      return { success: true };
    }
    case 'reload': {
      await chrome.tabs.reload(Number(params.tabId));
      return { success: true };
    }
    default:
      throw new Error(`Unknown tabs command: ${cmd}`);
  }
}

// ─── Navigation ───────────────────────────────────────────────

async function handleNav(cmd, params) {
  const tabId = Number(params.tabId || (await getActiveTabId()));

  switch (cmd) {
    case 'goto': {
      await chrome.tabs.update(tabId, { url: params.url });
      return { success: true };
    }
    case 'back': {
      await chrome.scripting.executeScript({
        target: { tabId },
        func: () => window.history.back(),
      });
      return { success: true };
    }
    case 'forward': {
      await chrome.scripting.executeScript({
        target: { tabId },
        func: () => window.history.forward(),
      });
      return { success: true };
    }
    case 'reload': {
      await chrome.tabs.reload(tabId);
      return { success: true };
    }
    default:
      throw new Error(`Unknown nav command: ${cmd}`);
  }
}

// ─── Cookies ──────────────────────────────────────────────────

async function handleCookies(cmd, params) {
  switch (cmd) {
    case 'getAll': {
      const query = {};
      if (params.domain) query.domain = params.domain;
      if (params.url) query.url = params.url;
      if (params.name) query.name = params.name;
      const cookies = await chrome.cookies.getAll(query);
      return cookies.map(c => ({
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path,
        secure: c.secure,
        httpOnly: c.httpOnly,
        sameSite: c.sameSite,
        expirationDate: c.expirationDate,
      }));
    }
    case 'set': {
      const cookie = await chrome.cookies.set({
        url: params.url,
        name: params.name,
        value: params.value,
        domain: params.domain,
        path: params.path || '/',
        secure: params.secure,
        httpOnly: params.httpOnly,
        sameSite: params.sameSite || 'lax',
        expirationDate: params.expirationDate,
      });
      return { success: !!cookie, cookie };
    }
    case 'remove': {
      const result = await chrome.cookies.remove({
        url: params.url,
        name: params.name,
      });
      return { success: !!result };
    }
    case 'clear': {
      // Clear all cookies for a domain
      const all = await chrome.cookies.getAll(
        params.domain ? { domain: params.domain } : {}
      );
      let removed = 0;
      for (const c of all) {
        const protocol = c.secure ? 'https' : 'http';
        const url = `${protocol}://${c.domain.replace(/^\./, '')}${c.path}`;
        try {
          await chrome.cookies.remove({ url, name: c.name });
          removed++;
        } catch { /* best effort */ }
      }
      return { success: true, removed };
    }
    default:
      throw new Error(`Unknown cookies command: ${cmd}`);
  }
}

// ─── DOM Interaction ────────────────────────────────────────

async function handleDom(cmd, params) {
  const tabId = Number(params.tabId || (await getActiveTabId()));

  switch (cmd) {
    case 'enumerate': {
      const maxDepth = params.maxDepth || 5;
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: enumerateDom,
        args: [maxDepth],
      });
      return results[0]?.result || [];
    }
    case 'query': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: queryDom,
        args: [params.selector || {}, params.limit || 50],
      });
      return results[0]?.result || [];
    }
    case 'click': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domAction,
        args: [params.elementId, 'click', {}],
      });
      return results[0]?.result || { success: false, error: 'No result' };
    }
    case 'doubleclick': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domAction,
        args: [params.elementId, 'doubleclick', {}],
      });
      return results[0]?.result || { success: false, error: 'No result' };
    }
    case 'type': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domAction,
        args: [params.elementId, 'type', { text: params.text }],
      });
      return results[0]?.result || { success: false, error: 'No result' };
    }
    case 'clear': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domAction,
        args: [params.elementId, 'clear', {}],
      });
      return results[0]?.result || { success: false, error: 'No result' };
    }
    case 'select': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domAction,
        args: [params.elementId, 'select', { value: params.value }],
      });
      return results[0]?.result || { success: false, error: 'No result' };
    }
    case 'focus': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domAction,
        args: [params.elementId, 'focus', {}],
      });
      return results[0]?.result || { success: false, error: 'No result' };
    }
    case 'hover': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domAction,
        args: [params.elementId, 'hover', {}],
      });
      return results[0]?.result || { success: false, error: 'No result' };
    }
    case 'scroll': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domAction,
        args: [params.elementId, 'scroll', { direction: params.direction, amount: params.amount }],
      });
      return results[0]?.result || { success: false, error: 'No result' };
    }
    case 'check': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domAction,
        args: [params.elementId, 'check', {}],
      });
      return results[0]?.result || { success: false, error: 'No result' };
    }
    case 'uncheck': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domAction,
        args: [params.elementId, 'uncheck', {}],
      });
      return results[0]?.result || { success: false, error: 'No result' };
    }
    case 'getValue': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: domGetValue,
        args: [params.elementId],
      });
      return results[0]?.result || { value: null };
    }
    default:
      throw new Error(`Unknown dom command: ${cmd}`);
  }
}

// ─── Storage ─────────────────────────────────────────────────

async function handleStorage(cmd, params) {
  const tabId = Number(params.tabId || (await getActiveTabId()));
  const storageType = params.storageType || 'local'; // 'local' or 'session'

  switch (cmd) {
    case 'get': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: storageGet,
        args: [storageType, params.key],
      });
      return results[0]?.result;
    }
    case 'set': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: storageSet,
        args: [storageType, params.key, params.value],
      });
      return results[0]?.result || { success: true };
    }
    case 'remove': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: storageRemove,
        args: [storageType, params.key],
      });
      return results[0]?.result || { success: true };
    }
    case 'clear': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: storageClear,
        args: [storageType],
      });
      return results[0]?.result || { success: true };
    }
    case 'keys': {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: storageKeys,
        args: [storageType],
      });
      return results[0]?.result || [];
    }
    default:
      throw new Error(`Unknown storage command: ${cmd}`);
  }
}

// ─── Script Execution ─────────────────────────────────────────

async function handleExec(cmd, params) {
  if (cmd !== 'run') throw new Error(`Unknown exec command: ${cmd}`);

  const tabId = Number(params.tabId || (await getActiveTabId()));
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: new Function('return (' + params.script + ')'),
  });
  return results[0]?.result;
}

// ─── Screenshot ─────────────────────────────────────────────

async function handleCapture(cmd, params) {
  if (cmd !== 'screenshot') throw new Error(`Unknown capture command: ${cmd}`);

  const dataUrl = await chrome.tabs.captureVisibleTab(null, {
    format: params.format || 'png',
    quality: params.quality || 90,
  });
  // Return base64 data (strip the data:image/png;base64, prefix)
  const base64 = dataUrl.split(',')[1];
  return { data: base64, format: params.format || 'png' };
}

// ─── Helper: Get Active Tab ID ────────────────────────────────

async function getActiveTabId() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tabs.length === 0) throw new Error('No active tab found');
  return tabs[0].id;
}

// ─── Injected Functions ──────────────────────────────────────
// These functions are serialized and injected into page contexts
// via chrome.scripting.executeScript. They must be self-contained.

/**
 * Build a CSS selector path for an element (used as stable ID)
 */
function getCssPath(el) {
  const parts = [];
  let current = el;
  while (current && current.nodeType === 1) {
    let selector = current.tagName.toLowerCase();
    if (current.id && /^[a-zA-Z][\w-]*$/.test(current.id)) {
      selector += '#' + current.id;
      parts.unshift(selector);
      break;
    }
    // Count same-tag siblings before this element
    let nth = 1;
    let sib = current.previousElementSibling;
    while (sib) {
      if (sib.tagName === current.tagName) nth++;
      sib = sib.previousElementSibling;
    }
    // Check if nth-of-type is needed
    let hasSameTagSibling = false;
    sib = current.parentElement?.firstElementChild;
    while (sib) {
      if (sib !== current && sib.tagName === current.tagName) {
        hasSameTagSibling = true;
        break;
      }
      sib = sib.nextElementSibling;
    }
    if (hasSameTagSibling) selector += `:nth-of-type(${nth})`;
    parts.unshift(selector);
    current = current.parentElement;
  }
  return parts.join(' > ');
}

/**
 * Map an element type from tag name + attributes
 */
function mapElementType(el) {
  const tag = el.tagName;
  const role = el.getAttribute('role');

  // ARIA role mappings
  if (role) {
    const roleMap = {
      'button': 'button', 'link': 'link', 'textbox': 'textfield',
      'checkbox': 'checkbox', 'radio': 'radio', 'tab': 'tab',
      'tabpanel': 'tabpanel', 'menuitem': 'menuitem', 'menu': 'menu',
      'dialog': 'dialog', 'slider': 'slider', 'progressbar': 'progressbar',
      'treeitem': 'treeitem', 'tree': 'tree', 'listbox': 'list',
      'option': 'listitem', 'heading': 'heading', 'img': 'image',
      'separator': 'separator', 'toolbar': 'toolbar',
      'combobox': 'select', 'searchbox': 'textfield',
    };
    if (roleMap[role]) return roleMap[role];
  }

  // Tag-based mapping
  const tagMap = {
    'BUTTON': 'button', 'A': 'link', 'INPUT': null, 'SELECT': 'select',
    'TEXTAREA': 'textarea', 'H1': 'heading', 'H2': 'heading', 'H3': 'heading',
    'H4': 'heading', 'H5': 'heading', 'H6': 'heading', 'IMG': 'image',
    'LABEL': 'label', 'TABLE': 'table', 'TR': 'tablerow', 'TD': 'tablecell',
    'TH': 'tablecell', 'UL': 'list', 'OL': 'list', 'LI': 'listitem',
    'NAV': 'container', 'DIALOG': 'dialog', 'DETAILS': 'tree',
    'SUMMARY': 'treeitem', 'PROGRESS': 'progressbar',
  };

  if (tag === 'INPUT') {
    const inputType = (el.type || 'text').toLowerCase();
    if (inputType === 'checkbox') return 'checkbox';
    if (inputType === 'radio') return 'radio';
    if (inputType === 'range') return 'slider';
    if (inputType === 'submit' || inputType === 'button') return 'button';
    return 'textfield';
  }

  return tagMap[tag] || 'unknown';
}

/**
 * Get the accessible label for an element
 */
function getLabel(el) {
  // aria-label first
  const ariaLabel = el.getAttribute('aria-label');
  if (ariaLabel) return ariaLabel;

  // aria-labelledby
  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy) {
    const labelEl = document.getElementById(labelledBy);
    if (labelEl) return labelEl.textContent?.trim() || '';
  }

  // Associated <label> for form elements
  if (el.id) {
    const label = document.querySelector(`label[for="${el.id}"]`);
    if (label) return label.textContent?.trim() || '';
  }

  // title attribute
  if (el.title) return el.title;

  // alt for images
  if (el.tagName === 'IMG' && el.alt) return el.alt;

  // placeholder for inputs
  if (el.placeholder) return el.placeholder;

  // Inner text (limited to 100 chars)
  const text = el.textContent?.trim() || '';
  return text.length > 100 ? text.substring(0, 100) + '...' : text;
}

/**
 * Determine available actions for an element
 */
function getActions(el) {
  const actions = ['click', 'focus', 'hover'];
  const tag = el.tagName;

  if (tag === 'INPUT' || tag === 'TEXTAREA' || el.getAttribute('contenteditable') === 'true') {
    actions.push('type', 'clear');
  }
  if (tag === 'SELECT') {
    actions.push('select');
  }
  if (tag === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {
    actions.push('check', 'uncheck', 'toggle');
  }
  if (tag === 'DETAILS') {
    actions.push('expand', 'collapse');
  }
  actions.push('scroll');

  return actions;
}

/**
 * Enumerate interactive DOM elements
 */
function enumerateDom(maxDepth) {
  const INTERACTIVE_TAGS = new Set([
    'A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'LABEL',
    'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'IMG',
    'DETAILS', 'SUMMARY', 'DIALOG', 'VIDEO', 'AUDIO',
  ]);

  const INTERACTIVE_ROLES = new Set([
    'button', 'link', 'textbox', 'checkbox', 'radio', 'tab',
    'menuitem', 'option', 'combobox', 'slider', 'switch',
    'searchbox', 'spinbutton', 'treeitem', 'gridcell',
  ]);

  function isInteractive(el) {
    if (INTERACTIVE_TAGS.has(el.tagName)) return true;
    if (el.getAttribute('role') && INTERACTIVE_ROLES.has(el.getAttribute('role'))) return true;
    if (el.getAttribute('tabindex')) return true;
    if (el.getAttribute('contenteditable') === 'true') return true;
    if (el.onclick || el.getAttribute('onclick')) return true;
    // Check for click listeners via cursor style
    const style = window.getComputedStyle(el);
    if (style.cursor === 'pointer') return true;
    return false;
  }

  function walkDom(el, depth) {
    if (depth > maxDepth) return [];
    const elements = [];

    if (isInteractive(el) && el !== document.body && el !== document.documentElement) {
      const rect = el.getBoundingClientRect();
      const visible = rect.width > 0 && rect.height > 0 &&
        window.getComputedStyle(el).display !== 'none' &&
        window.getComputedStyle(el).visibility !== 'hidden';

      if (visible) {
        elements.push({
          id: getCssPath(el),
          type: mapElementType(el),
          label: getLabel(el),
          properties: {
            tagName: el.tagName.toLowerCase(),
            className: el.className || undefined,
            href: el.href || undefined,
            src: el.src || undefined,
            value: el.value || undefined,
            type: el.type || undefined,
            checked: el.checked,
            disabled: el.disabled,
            placeholder: el.placeholder || undefined,
          },
          bounds: {
            x: Math.round(rect.x + window.scrollX),
            y: Math.round(rect.y + window.scrollY),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
          children: [],
          actions: getActions(el),
          visible: true,
          enabled: !el.disabled,
        });
      }
    }

    // Walk children
    for (const child of el.children) {
      elements.push(...walkDom(child, depth + 1));
    }

    return elements;
  }

  // Helper functions must be declared here since this runs in page context
  function getCssPath(el) {
    const parts = [];
    let current = el;
    while (current && current.nodeType === 1) {
      let selector = current.tagName.toLowerCase();
      if (current.id && /^[a-zA-Z][\w-]*$/.test(current.id)) {
        selector += '#' + current.id;
        parts.unshift(selector);
        break;
      }
      let nth = 1;
      let sib = current.previousElementSibling;
      while (sib) {
        if (sib.tagName === current.tagName) nth++;
        sib = sib.previousElementSibling;
      }
      let hasSameTagSibling = false;
      sib = current.parentElement?.firstElementChild;
      while (sib) {
        if (sib !== current && sib.tagName === current.tagName) {
          hasSameTagSibling = true;
          break;
        }
        sib = sib.nextElementSibling;
      }
      if (hasSameTagSibling) selector += `:nth-of-type(${nth})`;
      parts.unshift(selector);
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  function mapElementType(el) {
    const tag = el.tagName;
    const role = el.getAttribute('role');
    if (role) {
      const roleMap = {
        'button': 'button', 'link': 'link', 'textbox': 'textfield',
        'checkbox': 'checkbox', 'radio': 'radio', 'tab': 'tab',
        'tabpanel': 'tabpanel', 'menuitem': 'menuitem', 'menu': 'menu',
        'dialog': 'dialog', 'slider': 'slider', 'progressbar': 'progressbar',
        'treeitem': 'treeitem', 'combobox': 'select', 'searchbox': 'textfield',
      };
      if (roleMap[role]) return roleMap[role];
    }
    if (tag === 'INPUT') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'range') return 'slider';
      if (t === 'submit' || t === 'button') return 'button';
      return 'textfield';
    }
    const tagMap = {
      'BUTTON': 'button', 'A': 'link', 'SELECT': 'select',
      'TEXTAREA': 'textarea', 'H1': 'heading', 'H2': 'heading',
      'H3': 'heading', 'H4': 'heading', 'H5': 'heading', 'H6': 'heading',
      'IMG': 'image', 'LABEL': 'label', 'TABLE': 'table',
      'TR': 'tablerow', 'TD': 'tablecell', 'TH': 'tablecell',
      'UL': 'list', 'OL': 'list', 'LI': 'listitem',
      'DIALOG': 'dialog', 'DETAILS': 'tree', 'SUMMARY': 'treeitem',
      'PROGRESS': 'progressbar',
    };
    return tagMap[tag] || 'unknown';
  }

  function getLabel(el) {
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) return ariaLabel;
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const lbl = document.getElementById(labelledBy);
      if (lbl) return lbl.textContent?.trim() || '';
    }
    if (el.id) {
      const lbl = document.querySelector(`label[for="${el.id}"]`);
      if (lbl) return lbl.textContent?.trim() || '';
    }
    if (el.title) return el.title;
    if (el.tagName === 'IMG' && el.alt) return el.alt;
    if (el.placeholder) return el.placeholder;
    const text = el.textContent?.trim() || '';
    return text.length > 100 ? text.substring(0, 100) + '...' : text;
  }

  function getActions(el) {
    const actions = ['click', 'focus', 'hover'];
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || el.getAttribute('contenteditable') === 'true') {
      actions.push('type', 'clear');
    }
    if (tag === 'SELECT') actions.push('select');
    if (tag === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {
      actions.push('check', 'uncheck', 'toggle');
    }
    if (tag === 'DETAILS') actions.push('expand', 'collapse');
    actions.push('scroll');
    return actions;
  }

  return walkDom(document.body, 0);
}

/**
 * Query DOM elements by selector criteria
 */
function queryDom(selector, limit) {
  // Helper functions (same as enumerateDom - must be self-contained)
  function getCssPath(el) {
    const parts = [];
    let current = el;
    while (current && current.nodeType === 1) {
      let s = current.tagName.toLowerCase();
      if (current.id && /^[a-zA-Z][\w-]*$/.test(current.id)) {
        s += '#' + current.id;
        parts.unshift(s);
        break;
      }
      let nth = 1;
      let sib = current.previousElementSibling;
      while (sib) { if (sib.tagName === current.tagName) nth++; sib = sib.previousElementSibling; }
      let hasSame = false;
      sib = current.parentElement?.firstElementChild;
      while (sib) { if (sib !== current && sib.tagName === current.tagName) { hasSame = true; break; } sib = sib.nextElementSibling; }
      if (hasSame) s += `:nth-of-type(${nth})`;
      parts.unshift(s);
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  function getLabel(el) {
    return el.getAttribute('aria-label') || el.title || el.alt ||
      el.placeholder || (el.textContent?.trim() || '').substring(0, 100);
  }

  function mapType(el) {
    const tag = el.tagName;
    const role = el.getAttribute('role');
    if (role === 'button') return 'button';
    if (role === 'link') return 'link';
    if (role === 'textbox' || role === 'searchbox') return 'textfield';
    if (tag === 'BUTTON') return 'button';
    if (tag === 'A') return 'link';
    if (tag === 'INPUT') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      return 'textfield';
    }
    if (tag === 'SELECT') return 'select';
    if (tag === 'TEXTAREA') return 'textarea';
    if (/^H[1-6]$/.test(tag)) return 'heading';
    if (tag === 'IMG') return 'image';
    return 'unknown';
  }

  // Build CSS selector from UAB selector
  let cssSelector = '*';
  if (selector.type) {
    const typeToTag = {
      'button': 'button, input[type="button"], input[type="submit"], [role="button"]',
      'link': 'a, [role="link"]',
      'textfield': 'input:not([type="checkbox"]):not([type="radio"]):not([type="submit"]):not([type="button"]), [role="textbox"], [role="searchbox"]',
      'textarea': 'textarea',
      'checkbox': 'input[type="checkbox"], [role="checkbox"]',
      'radio': 'input[type="radio"], [role="radio"]',
      'select': 'select, [role="combobox"]',
      'image': 'img',
      'heading': 'h1, h2, h3, h4, h5, h6, [role="heading"]',
      'label': 'label',
    };
    cssSelector = typeToTag[selector.type] || `[role="${selector.type}"]`;
  }

  const allEls = Array.from(document.querySelectorAll(cssSelector));
  let filtered = allEls;

  // Filter by label text
  if (selector.label) {
    const lowerLabel = selector.label.toLowerCase();
    filtered = filtered.filter(el => {
      const label = getLabel(el).toLowerCase();
      return label.includes(lowerLabel);
    });
  }
  if (selector.labelExact) {
    filtered = filtered.filter(el => {
      return getLabel(el).trim() === selector.labelExact;
    });
  }
  if (selector.labelRegex) {
    const re = new RegExp(selector.labelRegex, 'i');
    filtered = filtered.filter(el => re.test(getLabel(el)));
  }

  // Filter visible only
  if (selector.visible !== false) {
    filtered = filtered.filter(el => {
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 &&
        window.getComputedStyle(el).display !== 'none' &&
        window.getComputedStyle(el).visibility !== 'hidden';
    });
  }

  // Apply limit
  filtered = filtered.slice(0, limit);

  return filtered.map(el => {
    const rect = el.getBoundingClientRect();
    return {
      id: getCssPath(el),
      type: mapType(el),
      label: getLabel(el),
      properties: {
        tagName: el.tagName.toLowerCase(),
        className: el.className || undefined,
        href: el.href || undefined,
        value: el.value || undefined,
      },
      bounds: {
        x: Math.round(rect.x + window.scrollX),
        y: Math.round(rect.y + window.scrollY),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      children: [],
      actions: ['click', 'focus'],
      visible: true,
      enabled: !el.disabled,
    };
  });
}

/**
 * Perform an action on a DOM element by its CSS path ID
 */
function domAction(elementId, action, params) {
  const el = document.querySelector(elementId);
  if (!el) return { success: false, error: `Element not found: ${elementId}` };

  switch (action) {
    case 'click':
      el.click();
      return { success: true };

    case 'doubleclick': {
      const evt = new MouseEvent('dblclick', { bubbles: true, cancelable: true });
      el.dispatchEvent(evt);
      return { success: true };
    }

    case 'type': {
      el.focus();
      // Set value directly and dispatch input event for React/Vue compatibility
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
      )?.set || Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype, 'value'
      )?.set;
      if (nativeInputValueSetter) {
        nativeInputValueSetter.call(el, (el.value || '') + (params.text || ''));
      } else {
        el.value = (el.value || '') + (params.text || '');
      }
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { success: true };
    }

    case 'clear': {
      el.focus();
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
      )?.set || Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype, 'value'
      )?.set;
      if (setter) {
        setter.call(el, '');
      } else {
        el.value = '';
      }
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { success: true };
    }

    case 'select': {
      if (el.tagName === 'SELECT') {
        el.value = params.value;
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return { success: true };
      }
      return { success: false, error: 'Element is not a <select>' };
    }

    case 'focus':
      el.focus();
      return { success: true };

    case 'hover': {
      el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
      el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      return { success: true };
    }

    case 'scroll': {
      const amount = params.amount || 200;
      const dir = params.direction || 'down';
      const opts = { behavior: 'smooth' };
      if (dir === 'down') el.scrollBy({ ...opts, top: amount });
      else if (dir === 'up') el.scrollBy({ ...opts, top: -amount });
      else if (dir === 'right') el.scrollBy({ ...opts, left: amount });
      else if (dir === 'left') el.scrollBy({ ...opts, left: -amount });
      return { success: true };
    }

    case 'check':
      if (el.type === 'checkbox' || el.type === 'radio') {
        if (!el.checked) el.click();
        return { success: true, checked: true };
      }
      return { success: false, error: 'Not a checkbox/radio' };

    case 'uncheck':
      if (el.type === 'checkbox') {
        if (el.checked) el.click();
        return { success: true, checked: false };
      }
      return { success: false, error: 'Not a checkbox' };

    default:
      return { success: false, error: `Unknown action: ${action}` };
  }
}

/**
 * Get the current value of a DOM element
 */
function domGetValue(elementId) {
  const el = document.querySelector(elementId);
  if (!el) return { value: null, error: `Element not found: ${elementId}` };
  return {
    value: el.value ?? el.textContent?.trim() ?? null,
    checked: el.checked,
    selected: el.selected,
  };
}

// ─── Storage Injected Functions ──────────────────────────────

function storageGet(type, key) {
  const s = type === 'session' ? sessionStorage : localStorage;
  if (key) return { key, value: s.getItem(key) };
  // Return all
  const all = {};
  for (let i = 0; i < s.length; i++) {
    const k = s.key(i);
    all[k] = s.getItem(k);
  }
  return all;
}

function storageSet(type, key, value) {
  const s = type === 'session' ? sessionStorage : localStorage;
  s.setItem(key, value);
  return { success: true };
}

function storageRemove(type, key) {
  const s = type === 'session' ? sessionStorage : localStorage;
  s.removeItem(key);
  return { success: true };
}

function storageClear(type) {
  const s = type === 'session' ? sessionStorage : localStorage;
  s.clear();
  return { success: true };
}

function storageKeys(type) {
  const s = type === 'session' ? sessionStorage : localStorage;
  const keys = [];
  for (let i = 0; i < s.length; i++) keys.push(s.key(i));
  return keys;
}

// ─── Initialize ──────────────────────────────────────────────

connect();
console.log('[UAB] Extension service worker loaded');
