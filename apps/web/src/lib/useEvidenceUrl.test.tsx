import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ApiContext } from './api-context';
import { createApiClient } from './api';
import { useEvidenceUrl } from './useEvidenceUrl';
const createUrl = vi.fn();
const revokeUrl = vi.fn();
beforeEach(() => {
  createUrl.mockReset().mockReturnValue('blob:private-evidence');
  revokeUrl.mockReset();
  vi.stubGlobal(
    'URL',
    Object.assign(URL, {
      createObjectURL: createUrl,
      revokeObjectURL: revokeUrl,
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function wrapper({ children }: { children: ReactNode }) {
  return (
    <ApiContext.Provider
      value={createApiClient({ getToken: async () => 'current-session-token' })}
    >
      {children}
    </ApiContext.Provider>
  );
}
describe('private evidence lifecycle', () => {
  it('uses authenticated blob URLs and revokes them on unmount', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('image'));
    vi.stubGlobal('fetch', fetchMock);
    const view = renderHook(() => useEvidenceUrl('/api/evidence/private'), {
      wrapper,
    });
    expect(view.result.current).toBeUndefined();
    await waitFor(() =>
      expect(view.result.current).toBe('blob:private-evidence'),
    );
    expect(fetchMock.mock.calls[0][1].headers.get('Authorization')).toBe(
      'Bearer current-session-token',
    );
    expect(fetchMock.mock.calls[0][0]).toBe('/api/evidence/private');
    view.unmount();
    expect(revokeUrl).toHaveBeenCalledWith('blob:private-evidence');
  });
  it('never falls back to an unauthenticated private image URL after denial', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('{}', { status: 403 })),
    );
    const view = renderHook(() => useEvidenceUrl('/api/evidence/private'), {
      wrapper,
    });
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(view.result.current).toBeUndefined();
    expect(createUrl).not.toHaveBeenCalled();
  });
});
