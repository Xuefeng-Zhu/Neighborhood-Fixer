/** Public deployment configuration. Clerk owns token storage and renewal. */
export function apiUrl(path: string) {
  const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
  return `${base}${path.startsWith('/api/') ? path : `/api${path}`}`;
}
export function clerkPublishableKey() {
  return import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim() || '';
}
