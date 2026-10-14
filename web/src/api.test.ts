import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, request, setToken, TOKEN_KEY } from './api';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
describe('workspace request boundary', () => {
  it('sends the session token only as an authorization header', async () => {
    setToken('  temporary-workspace-token  ');
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetcher);
    await request('/systems');
    expect(fetcher.mock.calls[0][0]).toBe('/api/systems');
    const options = fetcher.mock.calls[0][1] as RequestInit;
    expect(new Headers(options.headers).get('Authorization')).toBe(
      'Bearer temporary-workspace-token',
    );
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('temporary-workspace-token');
    expect(localStorage.length).toBe(0);
    setToken('');
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
  });
  it('requests authentication on 401 and preserves the server explanation', async () => {
    const listener = vi.fn();
    window.addEventListener('workspace:auth-required', listener);
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: 'Workspace token required' }), { status: 401 }),
        ),
    );
    await expect(request('/collections')).rejects.toMatchObject({
      message: 'Workspace token required',
      status: 401,
    });
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener('workspace:auth-required', listener);
  });
  it('lets the browser set multipart boundaries on source uploads', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetcher);
    const form = new FormData();
    form.append('file', new File(['invoice'], 'invoice.txt'));
    await request('/sources', { method: 'POST', body: form });
    expect(new Headers(fetcher.mock.calls[0][1].headers).has('Content-Type')).toBe(false);
  });
  it('handles a successful empty delete response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    expect(await request('/systems/example', { method: 'DELETE' })).toBeUndefined();
  });
  it('keeps non-JSON proxy failures readable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Unavailable', { status: 503 })));
    await expect(request('/overview')).rejects.toEqual(new ApiError('Request failed (503).', 503));
  });
});
