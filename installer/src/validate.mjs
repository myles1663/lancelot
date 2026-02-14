// ============================================================
// Validate — API key validation via lightweight HTTP probes
// ============================================================

export async function validateApiKey(provider, apiKey) {
  const timeout = 10000;

  try {
    if (provider === 'gemini') {
      const url = `https://generativelanguage.googleapis.com/v1beta/models?key=${apiKey}`;
      const res = await fetch(url, { signal: AbortSignal.timeout(timeout) });
      if (res.ok) return { valid: true };
      if (res.status === 400 || res.status === 403) {
        return { valid: false, error: 'Invalid API key' };
      }
      return { valid: false, error: `Unexpected response: ${res.status}` };
    }

    if (provider === 'openai') {
      const res = await fetch('https://api.openai.com/v1/models', {
        headers: { 'Authorization': `Bearer ${apiKey}` },
        signal: AbortSignal.timeout(timeout),
      });
      if (res.ok) return { valid: true };
      if (res.status === 401) return { valid: false, error: 'Invalid API key' };
      return { valid: false, error: `Unexpected response: ${res.status}` };
    }

    if (provider === 'anthropic') {
      const res = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'x-api-key': apiKey,
          'anthropic-version': '2023-06-01',
          'content-type': 'application/json',
        },
        body: JSON.stringify({ model: 'claude-3-5-haiku-latest', max_tokens: 1, messages: [{ role: 'user', content: 'hi' }] }),
        signal: AbortSignal.timeout(timeout),
      });
      // 401 = invalid key; anything else (200, 400, 429) means key is valid
      if (res.status === 401) return { valid: false, error: 'Invalid API key' };
      return { valid: true };
    }

    return { valid: false, error: `Unknown provider: ${provider}` };
  } catch (e) {
    // Network error — don't block install, key might still be valid
    return { valid: true, warning: `Could not reach ${provider} API to validate key: ${e.message}` };
  }
}
