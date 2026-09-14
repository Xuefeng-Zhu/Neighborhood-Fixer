// @refresh reset
import { useState } from 'react';
import { SessionContext as Context } from './session-context';
export { useSession } from './session-context';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, post, request } from './api';
import type { Health, Session } from './api';
import type { ReactNode } from 'react';
import { authConfigured, finishSignIn, startSignIn } from './auth';
export function SessionProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [bootError, setBootError] = useState('');
  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => request<Health>('/health'),
    retry: 1,
  });
  const session = useQuery({
    queryKey: ['session'],
    enabled: Boolean(health.data),
    retry: false,
    queryFn: async () => {
      try {
        if (!health.data?.mode.toLowerCase().includes('local'))
          await finishSignIn();
        return await request<Session>('/session');
      } catch (error) {
        if (
          !(error instanceof ApiError) ||
          error.status !== 401 ||
          !health.data?.mode.toLowerCase().includes('local')
        )
          throw error;
        const session = await post<Session>('/demo/session', {
          resident: 'alex',
        });
        localStorage.setItem('nf-workspace', session.workspace_id);
        return session;
      }
    },
  });
  async function switchResident(resident: string) {
    try {
      const result = await post<Session>('/demo/session', {
        resident,
        workspace_id: session.data?.workspace_id,
      });
      localStorage.setItem('nf-workspace', result.workspace_id);
      await client.cancelQueries();
      client.removeQueries({
        predicate: (query) =>
          !['health', 'session'].includes(String(query.queryKey[0])),
      });
      client.setQueryData(['session'], result);
      setBootError('');
    } catch (error) {
      setBootError((error as Error).message);
    }
  }
  if (
    session.error &&
    health.data &&
    !health.data.mode.toLowerCase().includes('local')
  )
    return (
      <main className="boot-screen">
        <div className="brand-symbol" />
        <h1>Your neighborhood, together.</h1>
        <p>
          Sign in to Neighborhood Fixer to view your cases and control what gets
          shared.
        </p>
        <p className="muted small">
          AWS demo — the receiving agency is fictional.
        </p>
        {bootError && <p role="alert">{bootError}</p>}
        <p className="small muted">
          {authConfigured()
            ? 'Secure sign-in is provided by Amazon Cognito.'
            : 'Cognito sign-in is unavailable. Configure the domain, public client ID and registered redirect URL.'}
        </p>
        <button
          disabled={!authConfigured()}
          onClick={() =>
            void startSignIn().catch((error) =>
              setBootError((error as Error).message),
            )
          }
        >
          Sign in with Cognito
        </button>
      </main>
    );
  if (health.error || session.error)
    return (
      <main className="boot-screen">
        <div className="brand-symbol" />
        <h1>Let’s reconnect.</h1>
        <p>{(health.error || session.error)?.message}</p>
        <p className="muted">
          Start the local services with <code>npm run dev</code>. AWS mode
          requires your configured sign-in.
        </p>
        <button
          onClick={() => {
            void health.refetch();
            void session.refetch();
          }}
        >
          Try again
        </button>
      </main>
    );
  if (!health.data || !session.data)
    return (
      <main className="boot-screen" role="status">
        <div className="brand-symbol" />
        <h1>Neighborhood Fixer</h1>
        <p>Opening your neighborhood…</p>
      </main>
    );
  return (
    <Context.Provider
      value={{ session: session.data, health: health.data, switchResident }}
    >
      {bootError && (
        <div role="alert" className="error-banner">
          {bootError}
        </div>
      )}
      {children}
    </Context.Provider>
  );
}
