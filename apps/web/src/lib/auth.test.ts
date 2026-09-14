import { afterEach, describe, expect, it, vi } from 'vitest';
import { getAccessToken, finishSignIn } from './auth';
afterEach(() => {
  sessionStorage.clear();
  window.history.replaceState({}, '', '/');
  vi.unstubAllGlobals();
});
describe('Cognito client authorization boundaries', () => {
  it('clears expired credentials rather than presenting an active session', () => {
    sessionStorage.setItem(
      'nf-cognito-access',
      JSON.stringify({
        accessToken: 'expired-test-token',
        expiresAt: Date.now() - 1,
      }),
    );
    expect(getAccessToken()).toBeUndefined();
    expect(sessionStorage.getItem('nf-cognito-access')).toBeNull();
  });
  it('returns only the short-lived access token while valid', () => {
    sessionStorage.setItem(
      'nf-cognito-access',
      JSON.stringify({
        accessToken: 'access-test-token',
        expiresAt: Date.now() + 60000,
      }),
    );
    expect(getAccessToken()).toBe('access-test-token');
  });
  it('rejects a mismatched OAuth callback state before any token request', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    sessionStorage.setItem(
      'nf-cognito-pkce',
      JSON.stringify({
        verifier: 'test-verifier',
        state: 'expected-state',
        createdAt: Date.now(),
      }),
    );
    window.history.replaceState(
      {},
      '',
      '/?code=untrusted-code&state=wrong-state',
    );
    await expect(finishSignIn()).rejects.toThrow(
      'Sign-in could not be verified',
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(sessionStorage.getItem('nf-cognito-pkce')).toBeNull();
    expect(window.location.search).toBe('');
  });
});
