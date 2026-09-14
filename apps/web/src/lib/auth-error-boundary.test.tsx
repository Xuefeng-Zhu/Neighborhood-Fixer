import { lazy, Suspense } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { AuthErrorBoundary } from './auth-error-boundary';
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it('offers an accessible reload when the lazy auth chunk rejects before it can render', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const FailedChunk = lazy(() =>
    Promise.reject(new Error('Simulated missing auth chunk')),
  );
  const reload = vi.fn();
  render(
    <AuthErrorBoundary onReload={reload}>
      <Suspense fallback={<p role="status">Loading sign-in…</p>}>
        <FailedChunk />
      </Suspense>
    </AuthErrorBoundary>,
  );
  expect(
    await screen.findByRole('heading', { name: 'Sign-in could not load.' }),
  ).toBeVisible();
  expect(
    screen.getByText(
      'Reload the page to reconnect. Your saved draft is unchanged.',
    ),
  ).toBeVisible();
  expect(
    screen.queryByText('Simulated missing auth chunk'),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Reload page' }));
  expect(reload).toHaveBeenCalledTimes(1);
});
