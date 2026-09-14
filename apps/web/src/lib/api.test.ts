import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiError, isPending, request } from './api';
afterEach(() => vi.unstubAllGlobals());
describe('API failures and durable operation polling', () => {
  it('preserves actionable server errors and correlation IDs', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: 'STALE_APPROVAL',
              message: 'Review the latest revision.',
              retryable: false,
              correlation_id: 'correlation-1',
            },
          }),
          { status: 409 },
        ),
      ),
    );
    try {
      await request('/incidents/case/approve');
      throw new Error('Expected request failure');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({
        code: 'STALE_APPROVAL',
        message: 'Review the latest revision.',
        retryable: false,
        correlationId: 'correlation-1',
      });
    }
  });
  it('polls persisted queued/running operations and stops on terminal results', () => {
    expect(isPending('pending')).toBe(true);
    expect(isPending('running')).toBe(true);
    expect(isPending('completed')).toBe(false);
    expect(isPending('failed')).toBe(false);
  });
});
