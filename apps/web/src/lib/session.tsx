// @refresh reset
import { lazy, Suspense, useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, localApi, publicApi } from './api';
import type { Health, Session } from './api';
import { createDraftLifecycle } from './draft-lifecycle';
import { AuthErrorBoundary } from './auth-error-boundary';
const HostedAuth = lazy(() =>
  import('./hosted-auth').then((module) => ({ default: module.HostedAuth })),
);
import { SessionWorkspace, SessionScreen } from './session-workspace';
export { useSession } from './session-context';

export function SessionProvider({ children }: { children: ReactNode }) {
  const [draftLifecycle] = useState(createDraftLifecycle);
  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => publicApi.request<Health>('/health', { signal }),
    retry: 1,
  });
  if (health.error)
    return (
      <SessionScreen
        title="Let’s reconnect."
        message="Neighborhood Fixer could not reach its API."
        retry={() => void health.refetch()}
      />
    );
  if (!health.data) return <SessionScreen loading />;
  return health.data.mode.toLowerCase().includes('local') ? (
    <LocalSession health={health.data}>{children}</LocalSession>
  ) : (
    <AuthErrorBoundary>
      <Suspense fallback={<SessionScreen loading />}>
        <HostedAuth health={health.data} draftLifecycle={draftLifecycle}>
          {children}
        </HostedAuth>
      </Suspense>
    </AuthErrorBoundary>
  );
}
function LocalSession({
  health,
  children,
}: {
  health: Health;
  children: ReactNode;
}) {
  const client = useQueryClient();
  const [error, setError] = useState('');
  const query = useQuery({
    queryKey: ['local-session'],
    retry: false,
    queryFn: async () => {
      try {
        return await localApi.request<Session>('/session');
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) throw error;
        return localApi.post<Session>('/demo/session', { resident: 'alex' });
      }
    },
  });
  async function switchResident(resident: string) {
    try {
      const result = await localApi.post<Session>('/demo/session', {
        resident,
        workspace_id: query.data?.workspace_id,
      });
      localStorage.setItem('nf-workspace', result.workspace_id);
      client.setQueryData(['local-session'], result);
      setError('');
    } catch (error) {
      setError((error as Error).message);
    }
  }
  if (query.error)
    return (
      <SessionScreen
        title="Let’s reconnect."
        message={query.error.message}
        retry={() => void query.refetch()}
      />
    );
  if (!query.data) return <SessionScreen loading />;
  return (
    <>
      {error && (
        <div role="alert" className="error-banner">
          {error}
        </div>
      )}
      <SessionWorkspace
        key={`${query.data.workspace_id}:${query.data.user.id}`}
        health={health}
        initialSession={query.data}
        switchResident={switchResident}
      >
        {children}
      </SessionWorkspace>
    </>
  );
}
