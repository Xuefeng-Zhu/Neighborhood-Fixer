import { afterEach, describe, expect, it, vi } from 'vitest';
import { creationAttempt } from './idempotency';
import { createApiClient } from './api';
afterEach(() => vi.unstubAllGlobals());
describe('resident action retry identity', () => {
  it('preserves a report key through serialization and changes it only for an edited payload', () => {
    const first = creationAttempt({
      description: 'An uneven curb',
      evidence_ids: ['photo-a'],
    });
    const restored = JSON.parse(JSON.stringify(first));
    expect(
      creationAttempt(
        { description: 'An uneven curb', evidence_ids: ['photo-a'] },
        restored,
      ).key,
    ).toBe(first.key);
    expect(
      creationAttempt(
        { description: 'A revised observation', evidence_ids: ['photo-a'] },
        restored,
      ).key,
    ).not.toBe(first.key);
  });
  it('uses the same upload key for a reselected identical file after a failed response', async () => {
    vi.stubGlobal('crypto', {
      ...crypto,
      randomUUID: vi
        .fn()
        .mockReturnValue('12345678-1234-4123-8123-123456789abc'),
      subtle: {
        digest: vi.fn().mockResolvedValue(new Uint8Array([1, 2, 3]).buffer),
      },
    });
    const file = () =>
      Object.assign(new File(['photo'], 'photo.jpg'), {
        arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer,
      });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('{}', { status: 503 }))
      .mockResolvedValueOnce(new Response('{"id":"photo-a"}'));
    vi.stubGlobal('fetch', fetchMock);
    const api = createApiClient({ getToken: async () => 'token' });
    await expect(api.upload(file())).rejects.toMatchObject({ status: 503 });
    await expect(api.upload(file())).resolves.toEqual({ id: 'photo-a' });
    expect(fetchMock.mock.calls[0][1].headers.get('Idempotency-Key')).toBe(
      fetchMock.mock.calls[1][1].headers.get('Idempotency-Key'),
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
