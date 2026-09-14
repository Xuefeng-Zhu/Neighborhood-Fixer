/** Cognito authorization-code flow with S256 PKCE. No client secret or ID-token-derived authorization. */
const tokenKey = 'nf-cognito-access';
const transactionKey = 'nf-cognito-pkce';
const config = () => ({
  domain: import.meta.env.VITE_COGNITO_DOMAIN,
  clientId: import.meta.env.VITE_COGNITO_CLIENT_ID,
  redirectUri:
    import.meta.env.VITE_COGNITO_REDIRECT_URI || `${window.location.origin}/`,
});
export function authConfigured() {
  const c = config();
  return Boolean(c.domain && c.clientId && c.redirectUri);
}
function safeDomain() {
  const c = config();
  if (!authConfigured())
    throw new Error(
      'Cognito sign-in is not configured. Set the frontend Cognito domain, client ID, and registered redirect URI.',
    );
  const url = new URL(
    c.domain.startsWith('https://') ? c.domain : `https://${c.domain}`,
  );
  if (
    url.protocol !== 'https:' ||
    url.username ||
    url.password ||
    url.pathname !== '/'
  )
    throw new Error('Cognito domain must be a trusted HTTPS origin.');
  return url.origin;
}
function base64url(bytes: Uint8Array) {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}
export async function startSignIn() {
  const domain = safeDomain();
  const c = config();
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(48)));
  const state = base64url(crypto.getRandomValues(new Uint8Array(24)));
  const challenge = base64url(
    new Uint8Array(
      await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)),
    ),
  );
  sessionStorage.setItem(
    transactionKey,
    JSON.stringify({
      verifier,
      state,
      createdAt: Date.now(),
      returnPath: window.location.pathname + window.location.hash,
    }),
  );
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: c.clientId,
    redirect_uri: c.redirectUri,
    scope: 'openid email profile',
    state,
    code_challenge_method: 'S256',
    code_challenge: challenge,
  });
  window.location.assign(`${domain}/oauth2/authorize?${params}`);
}
export function getAccessToken(): string | undefined {
  try {
    const token = JSON.parse(sessionStorage.getItem(tokenKey) || 'null');
    if (token && token.expiresAt > Date.now() + 30000) return token.accessToken;
    sessionStorage.removeItem(tokenKey);
  } catch {
    sessionStorage.removeItem(tokenKey);
  }
  return undefined;
}
let callback: Promise<void> | undefined;
export async function finishSignIn() {
  if (callback) return callback;
  const params = new URLSearchParams(window.location.search);
  if (!params.has('code') && !params.has('error')) return;
  callback = (async () => {
    const code = params.get('code');
    const state = params.get('state');
    const raw = sessionStorage.getItem(transactionKey);
    sessionStorage.removeItem(transactionKey);
    const transaction = raw ? JSON.parse(raw) : null;
    window.history.replaceState({}, '', window.location.pathname);
    if (
      !transaction ||
      state !== transaction.state ||
      Date.now() - transaction.createdAt > 10 * 60 * 1000
    )
      throw new Error(
        'Sign-in could not be verified or has expired. Please sign in again.',
      );
    if (params.has('error') || !code)
      throw new Error('Sign-in was not completed. Your cases are unchanged.');
    const c = config();
    const response = await fetch(`${safeDomain()}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        client_id: c.clientId,
        code,
        redirect_uri: c.redirectUri,
        code_verifier: transaction.verifier,
      }),
      credentials: 'omit',
    });
    if (!response.ok)
      throw new Error(
        'Cognito could not complete sign-in. Please start sign-in again.',
      );
    const result = await response.json();
    if (
      typeof result.access_token !== 'string' ||
      typeof result.expires_in !== 'number'
    )
      throw new Error('Cognito returned an incomplete sign-in response.');
    sessionStorage.setItem(
      tokenKey,
      JSON.stringify({
        accessToken: result.access_token,
        expiresAt: Date.now() + result.expires_in * 1000,
      }),
    );
  })();
  return callback;
}
export function signOut() {
  sessionStorage.removeItem(tokenKey);
  sessionStorage.removeItem(transactionKey);
  const c = config();
  const params = new URLSearchParams({
    client_id: c.clientId,
    logout_uri: c.redirectUri,
  });
  window.location.assign(`${safeDomain()}/logout?${params}`);
}
export function apiUrl(path: string) {
  const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
  return `${base}${path.startsWith('/api') ? path : `/api${path}`}`;
}
