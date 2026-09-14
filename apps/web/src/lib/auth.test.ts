import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApiClient } from './api';
afterEach(() => vi.unstubAllGlobals());
describe('Clerk token transport', () => {
  it('awaits the current token for every request instead of caching credentials', async () => {
    const getToken = vi
      .fn()
      .mockResolvedValueOnce('first-token')
      .mockResolvedValueOnce('renewed-token');
    const fetchMock = vi
      .fn()
      .mockImplementation(async () => new Response('{}'));
    vi.stubGlobal('fetch', fetchMock);
    const api = createApiClient({ getToken });
    await api.request('/session');
    await api.post('/incidents/case/approve', { approved: true });
    expect(getToken).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1].headers.get('Authorization')).toBe(
      'Bearer first-token',
    );
    expect(fetchMock.mock.calls[1][1].headers.get('Authorization')).toBe(
      'Bearer renewed-token',
    );
    expect(fetchMock.mock.calls[0][1].credentials).toBe('omit');
    expect(fetchMock.mock.calls[0][1].headers.has('Content-Type')).toBe(false);
  });
  it('fails closed when no session token exists and never sends a caller-supplied principal', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const api = createApiClient({ getToken: async () => null });
    await expect(
      api.request('/session', { headers: { Authorization: 'untrusted' } }),
    ).rejects.toMatchObject({ status: 401, code: 'SIGN_IN_REQUIRED' });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('prevents a pending token request from sending after identity cleanup', async () => {
    let resolve!: (value: string) => void;
    const api = createApiClient({
      getToken: () =>
        new Promise<string>((done) => {
          resolve = done;
        }),
    });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const pending = api.post('/observations', {});
    api.dispose();
    resolve('old-account-token');
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('uses the async token for multipart uploads and private evidence', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async () => new Response('{}'));
    vi.stubGlobal('fetch', fetchMock);
    const api = createApiClient({ getToken: async () => 'current-token' });
    await api.upload(
      new File(['photo'], 'photo.jpg', { type: 'image/jpeg' }),
      'stable-upload-key',
    );
    await api.evidence('/api/evidence/photo');
    expect(fetchMock.mock.calls[0][1].body).toBeInstanceOf(FormData);
    expect(fetchMock.mock.calls[0][1].headers.has('Content-Type')).toBe(false);
    expect(fetchMock.mock.calls[0][1].headers.get('Idempotency-Key')).toBe(
      'stable-upload-key',
    );
    expect(
      fetchMock.mock.calls.every(
        (call) =>
          call[1].headers.get('Authorization') === 'Bearer current-token',
      ),
    ).toBe(true);
  });
  it('does not retry an authenticated write after an HTTP failure', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response('{}', { status: 401 }));
    vi.stubGlobal('fetch', fetchMock);
    const api = createApiClient({ getToken: async () => 'current-token' });
    await expect(api.post('/observations', {})).rejects.toMatchObject({
      status: 401,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
