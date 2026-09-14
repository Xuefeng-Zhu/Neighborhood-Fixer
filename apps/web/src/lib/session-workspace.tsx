import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from '@tanstack/react-query';
import { ApiContext } from './api-context';
import { ApiError, createApiClient } from './api';
import type { ApiClient, Health, Session, TokenGetter } from './api';
import { SessionContext } from './session-context';
import { clearReportDraft, reportDraftKey } from './draft-lifecycle';

export function SessionScreen({
  title = 'Neighborhood Fixer',
  message,
  loading,
  retry,
  retryLabel = 'Try again',
}: {
  title?: string;
  message?: string;
  loading?: boolean;
  retry?: () => void;
  retryLabel?: string;
}) {
  return (
    <main
      className="boot-screen"
      role={loading ? 'status' : undefined}
      aria-live="polite"
    >
      <div className="brand-symbol" aria-hidden="true" />
      <h1>{title}</h1>
      <p>{loading ? 'Opening your neighborhood…' : message}</p>
      {retry && <button onClick={retry}>{retryLabel}</button>}
    </main>
  );
}
type WorkspaceProps = {
  children: ReactNode;
  health: Health;
  initialSession?: Session;
  getToken?: TokenGetter;
  signOut?: () => Promise<void>;
  onDraftKey?: (key: string) => void;
  switchResident?: (resident: string) => Promise<void>;
};
/** Remounted for every local resident or Clerk user/session transition. */
export function SessionWorkspace(props: WorkspaceProps) {
  const draftKey = useRef<string | undefined>(undefined);
  const recordSession = useCallback(
    (session: Session) => {
      draftKey.current = reportDraftKey(session);
      props.onDraftKey?.(draftKey.current);
    },
    [props.onDraftKey],
  );
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 2000,
            refetchOnWindowFocus: true,
            retry: (failures, error) =>
              failures < 1 &&
              !(error instanceof ApiError && [401, 403].includes(error.status)),
          },
        },
      }),
  );
  const [api] = useState(() =>
    createApiClient({
      getToken: props.getToken,
      onWrite: () => {
        if (props.getToken)
          void client.invalidateQueries({ queryKey: ['session'] });
      },
    }),
  );
  useLayoutEffect(() => {
    api.activate();
    return () => {
      api.dispose();
      void client.cancelQueries();
      client.clear();
    };
  }, [api, client]);
  return (
    <QueryClientProvider client={client}>
      <ApiContext.Provider value={api}>
        <WorkspaceSession
          {...props}
          api={api}
          recordSession={recordSession}
          clearDraft={() => clearReportDraft(draftKey.current)}
          clear={() => {
            api.dispose();
            void client.cancelQueries();
            client.clear();
          }}
        />
      </ApiContext.Provider>
    </QueryClientProvider>
  );
}
function WorkspaceSession({
  children,
  health,
  initialSession,
  api,
  signOut,
  switchResident,
  clear,
  clearDraft,
  recordSession,
  getToken,
}: WorkspaceProps & {
  api: ApiClient;
  clear: () => void;
  clearDraft: () => void;
  recordSession: (session: Session) => void;
}) {
  const [ending, setEnding] = useState(false);
  const [signOutError, setSignOutError] = useState(false);
  const session = useQuery({
    queryKey: ['session'],
    initialData: initialSession,
    retry: false,
    enabled: !initialSession && !ending,
    queryFn: ({ signal }) => api.request<Session>('/session', { signal }),
  });
  useLayoutEffect(() => {
    if (session.data) recordSession(session.data);
  }, [recordSession, session.data]);
  async function endSession() {
    setEnding(true);
    setSignOutError(false);
    clear();
    clearDraft();
    try {
      await signOut?.();
    } catch {
      setSignOutError(true);
    }
  }
  if (ending)
    return (
      <SessionScreen
        loading={!signOutError}
        title={signOutError ? 'Sign-out needs another try.' : 'Signing out…'}
        message="Private case data has been cleared from this screen."
        retry={signOutError ? () => void endSession() : undefined}
      />
    );
  if (session.error)
    return (
      <main className="boot-screen">
        <div className="brand-symbol" aria-hidden="true" />
        <h1>
          {session.error instanceof ApiError && session.error.status === 403
            ? 'Your account cannot open this neighborhood.'
            : 'We couldn’t open your neighborhood.'}
        </h1>
        <p role="alert">{session.error.message}</p>
        <button onClick={() => void session.refetch()}>Try again</button>
        {signOut && <button onClick={() => void endSession()}>Sign out</button>}
      </main>
    );
  if (!session.data) return <SessionScreen loading />;
  return (
    <BackendWorkspace
      key={`${session.data.workspace_id}:${session.data.generation || 'local'}:${session.data.user.id}`}
      session={session.data}
      health={health}
      getToken={getToken}
      refreshSession={() => {
        void session.refetch();
      }}
      switchResident={switchResident}
      signOut={signOut ? endSession : undefined}
    >
      {children}
    </BackendWorkspace>
  );
}
/** Server workspace/generation changes discard every cached case and in-flight action. */
function BackendWorkspace({
  children,
  session,
  health,
  getToken,
  refreshSession,
  switchResident,
  signOut,
}: {
  children: ReactNode;
  session: Session;
  health: Health;
  getToken?: TokenGetter;
  refreshSession: () => void;
  switchResident?: (resident: string) => Promise<void>;
  signOut?: () => Promise<void>;
}) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 2000,
            refetchOnWindowFocus: true,
            retry: (failures, error) =>
              failures < 1 &&
              !(error instanceof ApiError && [401, 403].includes(error.status)),
          },
        },
      }),
  );
  const [api] = useState(() =>
    createApiClient({
      getToken,
      onWrite: getToken ? refreshSession : undefined,
    }),
  );
  const clear = useCallback(() => {
    api.dispose();
    void client.cancelQueries();
    client.clear();
  }, [api, client]);
  useLayoutEffect(() => {
    api.activate();
    return clear;
  }, [api, clear]);
  return (
    <QueryClientProvider client={client}>
      <ApiContext.Provider value={api}>
        <SessionContext.Provider
          value={{
            session,
            health,
            switchResident:
              switchResident ||
              (async () => {
                throw new Error(
                  'Resident switching is only available in the local demo.',
                );
              }),
            signOut: signOut
              ? async () => {
                  clear();
                  await signOut();
                }
              : undefined,
          }}
        >
          {children}
        </SessionContext.Provider>
      </ApiContext.Provider>
    </QueryClientProvider>
  );
}
