import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';

import { UABConnector } from '../dist/connector.js';

function startDirectApiStub(port, handlers = {}) {
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {};

    res.setHeader('content-type', 'application/json');
    if (req.url === '/enumerate') {
      res.end(JSON.stringify(handlers.enumerate?.(body) ?? []));
      return;
    }
    if (req.url === '/query') {
      res.end(JSON.stringify(handlers.query?.(body) ?? []));
      return;
    }
    if (req.url === '/act') {
      res.end(JSON.stringify(handlers.act?.(body) ?? { success: true, result: { echoed: body.action } }));
      return;
    }
    if (req.url === '/state') {
      res.end(JSON.stringify(handlers.state?.(body) ?? {
        window: {
          title: 'Direct API Window',
          size: { width: 800, height: 600 },
          position: { x: 5, y: 10 },
          focused: true,
        },
        modals: [],
        menus: [],
      }));
      return;
    }

    res.statusCode = 404;
    res.end(JSON.stringify({ error: 'not found' }));
  });

  return new Promise((resolve) => {
    server.listen(port, '127.0.0.1', () => {
      const address = server.address();
      resolve({
        server,
        port: typeof address === 'object' && address ? address.port : port,
      });
    });
  });
}

test('connector uses direct-api when app profile advertises a direct API endpoint', async () => {
  const api = await startDirectApiStub(0, {
    enumerate: () => [{
      id: 'direct-root',
      type: 'window',
      label: 'Direct Root',
      properties: {},
      bounds: { x: 1, y: 2, width: 300, height: 200 },
      children: [],
      actions: ['click'],
      visible: true,
      enabled: true,
    }],
  });

  const connector = new UABConnector({
    persistent: false,
    extensionBridge: false,
    loadProfiles: false,
  });

  await connector.start();
  try {
    connector.registry.register({
      pid: 9001,
      name: 'Remote Spreadsheet',
      path: 'remote-spreadsheet.exe',
      framework: 'unknown',
      confidence: 1,
      connectionInfo: {
        directApi: {
          baseUrl: `http://127.0.0.1:${api.port}`,
          headers: { 'x-uab-test': 'true' },
        },
      },
    });

    const info = await connector.connect(9001);
    assert.equal(info.method, 'direct-api');

    const elements = await connector.enumerate(9001);
    assert.equal(elements.length, 1);
    assert.equal(elements[0].label, 'Direct Root');

    const state = await connector.state(9001);
    assert.equal(state.window.title, 'Direct API Window');
  } finally {
    await connector.stop();
    await new Promise((resolve, reject) => api.server.close((err) => err ? reject(err) : resolve()));
  }
});
