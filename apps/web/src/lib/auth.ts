/** Public deployment configuration. Clerk owns token storage and renewal. */
export function apiUrl(path: string) {
  const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
  return `${base}${path.startsWith('/api/') ? path : `/api${path}`}`;
}
/**
 * Transient audio has a dedicated streaming API in AWS. With no configured
 * base, local Vite development keeps using its same-origin /api proxy.
 */
export function audioApiUrl(path: string) {
  const configured = import.meta.env.VITE_AUDIO_API_BASE_URL?.trim() || '';
  if (!configured && import.meta.env.VITE_API_BASE_URL?.trim())
    throw new Error('The temporary audio streaming service is not configured.');
  const base = configured.replace(/\/$/, '');
  return `${base}${path.startsWith('/api/') ? path : `/api${path}`}`;
}
export function clerkPublishableKey() {
  return import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim() || '';
}
