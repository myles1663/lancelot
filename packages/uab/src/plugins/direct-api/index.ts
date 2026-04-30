import type {
  ActionType,
  ActionParams,
  ActionResult,
  AppState,
  DetectedApp,
  DirectApiConfig,
  ElementSelector,
  FrameworkPlugin,
  PluginConnection,
  Subscription,
  UIElement,
  UABEventCallback,
  UABEventType,
} from '../../types.js';

type FetchLike = (input: string, init?: Record<string, unknown>) => Promise<{
  ok: boolean;
  status: number;
  statusText: string;
  json(): Promise<unknown>;
  text(): Promise<string>;
}>;

function getFetch(): FetchLike {
  const fetchImpl = (globalThis as { fetch?: FetchLike }).fetch;
  if (!fetchImpl) {
    throw new Error('global fetch() is unavailable in this Node runtime');
  }
  return fetchImpl.bind(globalThis);
}

function readConfig(app: DetectedApp): DirectApiConfig | null {
  const directApi = app.connectionInfo?.directApi;
  if (!directApi || typeof directApi !== 'object') return null;

  const cfg = directApi as DirectApiConfig;
  if (!cfg.baseUrl || typeof cfg.baseUrl !== 'string') return null;
  return cfg;
}

export class DirectApiPlugin implements FrameworkPlugin {
  readonly framework = 'unknown' as const;
  readonly name = 'Direct API Plugin';
  readonly controlMethod = 'direct-api' as const;

  canHandle(app: DetectedApp): boolean {
    return !!readConfig(app);
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    const config = readConfig(app);
    if (!config) {
      throw new Error(`No directApi connection info is configured for ${app.name}`);
    }

    return new DirectApiConnection(app, config);
  }
}

class DirectApiConnection implements PluginConnection {
  readonly app: DetectedApp;
  readonly connected = true;
  private readonly config: DirectApiConfig;
  private readonly fetchImpl: FetchLike;

  constructor(app: DetectedApp, config: DirectApiConfig) {
    this.app = app;
    this.config = config;
    this.fetchImpl = getFetch();
  }

  async enumerate(): Promise<UIElement[]> {
    const response = await this.invoke('enumerate', {
      pid: this.app.pid,
      app: this.app,
    });
    return response as UIElement[];
  }

  async query(selector: ElementSelector): Promise<UIElement[]> {
    const response = await this.invoke('query', {
      pid: this.app.pid,
      app: this.app,
      selector,
    });
    return response as UIElement[];
  }

  async act(elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    const response = await this.invoke('act', {
      pid: this.app.pid,
      app: this.app,
      elementId,
      action,
      params,
    });
    return response as ActionResult;
  }

  async state(): Promise<AppState> {
    const response = await this.invoke('state', {
      pid: this.app.pid,
      app: this.app,
    });
    return response as AppState;
  }

  async subscribe(event: UABEventType, callback: UABEventCallback): Promise<Subscription> {
    void event;
    void callback;
    const id = `direct-api-nosub-${Date.now()}`;
    return {
      id,
      event,
      unsubscribe: () => undefined,
    };
  }

  async disconnect(): Promise<void> {
    return;
  }

  private endpointFor(operation: 'enumerate' | 'query' | 'act' | 'state'): string {
    const override = this.config.endpoints?.[operation];
    if (override) return override;
    return `/${operation}`;
  }

  private async invoke(
    operation: 'enumerate' | 'query' | 'act' | 'state',
    payload: Record<string, unknown>,
  ): Promise<unknown> {
    const base = this.config.baseUrl.replace(/\/+$/, '');
    const endpoint = this.endpointFor(operation).replace(/^\/?/, '/');
    const res = await this.fetchImpl(`${base}${endpoint}`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(this.config.headers || {}),
      },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Direct API ${operation} failed: ${res.status} ${res.statusText}${text ? ` - ${text}` : ''}`);
    }

    return res.json();
  }
}
