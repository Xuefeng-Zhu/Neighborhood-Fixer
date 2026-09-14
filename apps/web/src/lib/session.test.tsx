import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { StrictMode, useState } from 'react';
import type { ReactNode } from 'react';
import { SessionProvider, useSession } from './session';
import { useApi } from './api-context';
import { reportDraftKey } from './draft-lifecycle';
import type { Session } from './api';
const auth = vi.hoisted(() => ({
  isLoaded: true,
  isSignedIn: false,
  userId: null as string | null,
  sessionId: null as string | null,
  getToken: vi.fn(),
  signOut: vi.fn(),
  provider: vi.fn(),
}));
vi.mock('@clerk/react', () => ({
  ClerkProvider: ({ children }: { children: ReactNode }) => {
    auth.provider();
    return children;
  },
  ClerkLoaded: ({ children }: { children: ReactNode }) => children,
  ClerkLoading: () => null,
  ClerkFailed: () => null,
  SignIn: () => <h1>Sign in form</h1>,
  SignUp: () => <h1>Create account form</h1>,
  useAuth: () => auth,
}));
function Content() {
  const { session, signOut, switchResident } = useSession();
  const { request, post } = useApi();
  const [note, setNote] = useState('');
  const query = useQuery({
    queryKey: ['private-case'],
    queryFn: () => request<{ title: string }>('/private-case'),
  });
  return (
    <>
      <h1>{session.user.name} neighborhood</h1>
      <p>{query.data?.title}</p>
      <input
        aria-label="Transient note"
        value={note}
        onChange={(event) => setNote(event.target.value)}
      />
      {signOut && (
        <button onClick={() => void post('/test-write')}>
          Refresh backend session
        </button>
      )}
      {signOut ? (
        <button onClick={() => void signOut()}>Sign out</button>
      ) : (
        <button onClick={() => void switchResident('sam')}>
          Switch resident
        </button>
      )}
    </>
  );
}
const session = (id: string, mode = 'aws') => ({
  user: { id, name: id, resident: id },
  workspace_id: 'demo-borough',
  mode,
});
function hostedFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (url: string, init: RequestInit) => {
      if (url.endsWith('/health'))
        return new Response(JSON.stringify({ mode: 'aws', integrations: {} }));
      const subject =
        new Headers(init.headers)
          .get('Authorization')
          ?.replace('Bearer token-', '') || 'missing';
      const resident =
        subject === 'user_alex' ? 'backend-resident-a' : 'backend-resident-b';
      if (url.endsWith('/session'))
        return new Response(JSON.stringify(session(resident)));
      return new Response(
        JSON.stringify({ title: `${resident} private evidence` }),
      );
    }),
  );
}
function wrapper(client: QueryClient, path = '/') {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <SessionProvider>
          <Content />
        </SessionProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}
beforeEach(() => {
  Object.assign(auth, {
    isLoaded: true,
    isSignedIn: false,
    userId: null,
    sessionId: null,
  });
  auth.getToken
    .mockReset()
    .mockImplementation(async () => `token-${auth.userId}`);
  auth.signOut.mockReset().mockResolvedValue(undefined);
  auth.provider.mockClear();
  vi.stubEnv('VITE_CLERK_PUBLISHABLE_KEY', 'pk_test_test');
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  localStorage.clear();
});
describe('deployment and account session boundaries', () => {
  it('keeps local mode credential-free and never initializes Clerk', async () => {
    let user = 'alex';
    let initialized = false;
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('/health'))
        return new Response(
          JSON.stringify({ mode: 'local', integrations: {} }),
        );
      if (url.endsWith('/demo/session')) {
        user = initialized ? 'sam' : 'alex';
        initialized = true;
        return new Response(JSON.stringify(session(user, 'local')));
      }
      if (url.endsWith('/session'))
        return initialized
          ? new Response(JSON.stringify(session(user, 'local')))
          : new Response('{}', { status: 401 });
      return new Response(
        JSON.stringify({ title: `${user} private evidence` }),
      );
    });
    vi.stubGlobal('fetch', fetchMock);
    render(wrapper(new QueryClient()));
    expect(await screen.findByText('alex private evidence')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Switch resident' }));
    expect(await screen.findByText('sam private evidence')).toBeVisible();
    expect(screen.queryByText('alex private evidence')).not.toBeInTheDocument();
    expect(auth.provider).not.toHaveBeenCalled();
    expect(auth.getToken).not.toHaveBeenCalled();
    expect(
      fetchMock.mock.calls.every(
        (call) => !call[1].headers.has('Authorization'),
      ),
    ).toBe(true);
  });
  it('offers public sign-up and sign-in without fetching protected data', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ mode: 'aws', integrations: {} })),
      );
    vi.stubGlobal('fetch', fetchMock);
    render(wrapper(new QueryClient()));
    expect(
      await screen.findByRole('heading', {
        name: 'Your neighborhood, together.',
      }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('link', { name: 'Create account' }));
    expect(
      await screen.findByRole('heading', { name: 'Create account form' }),
    ).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(auth.getToken).not.toHaveBeenCalled();
  });
  it('clears the previous backend draft key when a different Clerk account becomes active', async () => {
    Object.assign(auth, {
      isSignedIn: true,
      userId: 'user_alex',
      sessionId: 'session-a',
    });
    hostedFetch();
    const client = new QueryClient();
    const view = render(wrapper(client));
    expect(
      await screen.findByText('backend-resident-a private evidence'),
    ).toBeVisible();
    localStorage.setItem(
      'nf-report:demo-borough:backend-resident-a',
      'Alex private draft',
    );
    localStorage.setItem(
      'nf-report:demo-borough:backend-resident-b',
      'Sam private draft',
    );
    localStorage.setItem('unrelated-setting', 'keep');
    Object.assign(auth, { userId: 'user_sam', sessionId: 'session-b' });
    view.rerender(wrapper(client));
    expect(
      screen.queryByText('backend-resident-a private evidence'),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByText('backend-resident-b private evidence'),
    ).toBeVisible();
    expect(
      localStorage.getItem('nf-report:demo-borough:backend-resident-a'),
    ).toBeNull();
    expect(
      localStorage.getItem('nf-report:demo-borough:backend-resident-b'),
    ).toBe('Sam private draft');
    expect(localStorage.getItem('unrelated-setting')).toBe('keep');
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() =>
      expect(auth.signOut).toHaveBeenCalledWith({ redirectUrl: '/' }),
    );
    expect(
      localStorage.getItem('nf-report:demo-borough:backend-resident-b'),
    ).toBeNull();
  });
  it('clears the backend draft on external sign-out without invoking sign-out a second time', async () => {
    Object.assign(auth, {
      isSignedIn: true,
      userId: 'user_alex',
      sessionId: 'session-a',
    });
    hostedFetch();
    const client = new QueryClient();
    const view = render(wrapper(client));
    expect(
      await screen.findByText('backend-resident-a private evidence'),
    ).toBeVisible();
    localStorage.setItem(
      'nf-report:demo-borough:backend-resident-a',
      'private draft',
    );
    Object.assign(auth, { isSignedIn: false, userId: null, sessionId: null });
    view.rerender(wrapper(client));
    expect(await screen.findByRole('link', { name: 'Sign in' })).toBeVisible();
    expect(
      localStorage.getItem('nf-report:demo-borough:backend-resident-a'),
    ).toBeNull();
    expect(auth.signOut).not.toHaveBeenCalled();
  });
  it('preserves a same-user draft through StrictMode, auth loading and a page remount', async () => {
    Object.assign(auth, {
      isSignedIn: true,
      userId: 'user_alex',
      sessionId: 'session-a',
    });
    hostedFetch();
    const key = 'nf-report:demo-borough:backend-resident-a';
    localStorage.setItem(key, 'resumable private draft');
    const client = new QueryClient();
    const view = render(<StrictMode>{wrapper(client)}</StrictMode>);
    expect(
      await screen.findByText('backend-resident-a private evidence'),
    ).toBeVisible();
    expect(localStorage.getItem(key)).toBe('resumable private draft');
    auth.isLoaded = false;
    view.rerender(<StrictMode>{wrapper(client)}</StrictMode>);
    expect(localStorage.getItem(key)).toBe('resumable private draft');
    auth.isLoaded = true;
    view.rerender(<StrictMode>{wrapper(client)}</StrictMode>);
    expect(
      await screen.findByText('backend-resident-a private evidence'),
    ).toBeVisible();
    view.unmount();
    render(<StrictMode>{wrapper(new QueryClient())}</StrictMode>);
    expect(
      await screen.findByText('backend-resident-a private evidence'),
    ).toBeVisible();
    expect(localStorage.getItem(key)).toBe('resumable private draft');
  });
  it('still invokes Clerk sign-out if browser storage cleanup throws', async () => {
    Object.assign(auth, {
      isSignedIn: true,
      userId: 'user_alex',
      sessionId: 'session-a',
    });
    hostedFetch();
    render(wrapper(new QueryClient()));
    expect(
      await screen.findByText('backend-resident-a private evidence'),
    ).toBeVisible();
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('Storage unavailable', 'SecurityError');
    });
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() =>
      expect(auth.signOut).toHaveBeenCalledWith({ redirectUrl: '/' }),
    );
    expect(
      screen.queryByText('backend-resident-a private evidence'),
    ).not.toBeInTheDocument();
  });
  it.each(['workspace', 'generation'] as const)(
    'discards old case state when the backend %s changes under the same Clerk login',
    async (change) => {
      Object.assign(auth, {
        isSignedIn: true,
        userId: 'user_alex',
        sessionId: 'session-a',
      });
      let workspace = 'demo-borough';
      let generation = 'generation-a';
      let oldRequestSignal: AbortSignal | undefined;
      vi.stubGlobal(
        'fetch',
        vi.fn().mockImplementation(async (url: string, init: RequestInit) => {
          if (url.endsWith('/health'))
            return new Response(
              JSON.stringify({ mode: 'aws', integrations: {} }),
            );
          if (url.endsWith('/session'))
            return new Response(
              JSON.stringify({
                ...session('backend-resident-a'),
                workspace_id: workspace,
                generation,
              }),
            );
          if (url.endsWith('/test-write')) return new Response('{}');
          oldRequestSignal ||= init.signal || undefined;
          return new Response(
            JSON.stringify({
              title: `${workspace} ${generation} private evidence`,
            }),
          );
        }),
      );
      render(wrapper(new QueryClient()));
      expect(
        await screen.findByText('demo-borough generation-a private evidence'),
      ).toBeVisible();
      const oldKey = reportDraftKey({
        ...session('backend-resident-a'),
        mode: 'aws',
        generation,
      } as Session);
      localStorage.setItem(oldKey, 'old-generation observation ID');
      fireEvent.change(screen.getByLabelText('Transient note'), {
        target: { value: 'Old private UI state' },
      });
      if (change === 'workspace') workspace = 'new-demo-borough';
      else generation = 'generation-b';
      fireEvent.click(
        screen.getByRole('button', { name: 'Refresh backend session' }),
      );
      expect(
        await screen.findByText(`${workspace} ${generation} private evidence`),
      ).toBeVisible();
      expect(
        screen.queryByText('demo-borough generation-a private evidence'),
      ).not.toBeInTheDocument();
      expect(screen.getByLabelText('Transient note')).toHaveValue('');
      expect(oldRequestSignal?.aborted).toBe(true);
      expect(localStorage.getItem(oldKey)).toBeNull();
    },
  );
  it('shows membership denial distinctly from signed-out login', async () => {
    Object.assign(auth, {
      isSignedIn: true,
      userId: 'alex',
      sessionId: 'session-a',
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) =>
        url.endsWith('/health')
          ? new Response(JSON.stringify({ mode: 'aws', integrations: {} }))
          : new Response(
              JSON.stringify({
                error: { message: 'Membership is disabled.' },
              }),
              { status: 403 },
            ),
      ),
    );
    render(wrapper(new QueryClient()));
    expect(
      await screen.findByRole('heading', {
        name: 'Your account cannot open this neighborhood.',
      }),
    ).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Membership is disabled.',
    );
    expect(
      screen.queryByRole('link', { name: 'Sign in' }),
    ).not.toBeInTheDocument();
  });
  it('does not initialize local credentials when hosted sign-in is unconfigured', async () => {
    vi.stubEnv('VITE_CLERK_PUBLISHABLE_KEY', '');
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ mode: 'aws', integrations: {} })),
      );
    vi.stubGlobal('fetch', fetchMock);
    render(wrapper(new QueryClient()));
    expect(
      await screen.findByRole('heading', { name: 'Sign-in is unavailable.' }),
    ).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(auth.provider).not.toHaveBeenCalled();
  });
});
