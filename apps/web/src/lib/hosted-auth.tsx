import { useCallback, useLayoutEffect } from 'react';
import type { ReactNode } from 'react';
import {
  ClerkProvider,
  ClerkFailed,
  ClerkLoading,
  ClerkLoaded,
  SignIn,
  SignUp,
  useAuth,
} from '@clerk/react';
import { Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { clerkPublishableKey } from './auth';
import type { Health } from './api';
import type { DraftLifecycle } from './draft-lifecycle';
import { SessionWorkspace, SessionScreen } from './session-workspace';

export function HostedAuth({
  health,
  children,
  draftLifecycle,
}: {
  health: Health;
  children: ReactNode;
  draftLifecycle: DraftLifecycle;
}) {
  const navigate = useNavigate();
  const publishableKey = clerkPublishableKey();
  if (!publishableKey)
    return (
      <SessionScreen
        title="Sign-in is unavailable."
        message="This hosted deployment needs its Clerk publishable key. Please contact the demo operator."
      />
    );
  return (
    <ClerkProvider
      publishableKey={publishableKey}
      routerPush={(to) => navigate(to)}
      routerReplace={(to) => navigate(to, { replace: true })}
      signInUrl="/sign-in"
      signUpUrl="/sign-up"
      signInFallbackRedirectUrl="/"
      signUpFallbackRedirectUrl="/"
      afterSignOutUrl="/"
      allowedRedirectOrigins={[window.location.origin]}
      appearance={{
        variables: {
          colorPrimary: '#176b5b',
          borderRadius: '0.65rem',
          fontFamily: 'inherit',
        },
      }}
    >
      <ClerkLoading>
        <SessionScreen loading />
      </ClerkLoading>
      <ClerkFailed>
        <SessionScreen
          title="Sign-in could not load."
          message="Check your connection and try again."
          retry={() => window.location.reload()}
        />
      </ClerkFailed>
      <ClerkLoaded>
        <HostedSession health={health} draftLifecycle={draftLifecycle}>
          {children}
        </HostedSession>
      </ClerkLoaded>
    </ClerkProvider>
  );
}
function HostedSession({
  health,
  children,
  draftLifecycle,
}: {
  health: Health;
  children: ReactNode;
  draftLifecycle: DraftLifecycle;
}) {
  const { isLoaded, isSignedIn, userId, sessionId, getToken, signOut } =
    useAuth();
  useLayoutEffect(() => {
    draftLifecycle.observeIdentity(isLoaded, isSignedIn, userId);
  }, [draftLifecycle, isLoaded, isSignedIn, userId]);
  const rememberDraft = useCallback(
    (key: string) => {
      if (userId) draftLifecycle.remember(userId, key);
    },
    [draftLifecycle, userId],
  );
  if (!isLoaded) return <SessionScreen loading />;
  if (!isSignedIn || !userId || !sessionId)
    return (
      <Routes>
        <Route
          path="/sign-in/*"
          element={
            <AuthPage>
              <SignIn routing="path" path="/sign-in" signUpUrl="/sign-up" />
            </AuthPage>
          }
        />
        <Route
          path="/sign-up/*"
          element={
            <AuthPage>
              <SignUp routing="path" path="/sign-up" signInUrl="/sign-in" />
            </AuthPage>
          }
        />
        <Route
          path="*"
          element={
            <AuthPage>
              <div className="auth-intro">
                <h1>Your neighborhood, together.</h1>
                <p>
                  Report a neighborhood issue, add evidence, and follow through
                  with your neighbors.
                </p>
                <div className="auth-actions">
                  <Link className="button primary" to="/sign-in">
                    Sign in
                  </Link>
                  <Link className="button secondary" to="/sign-up">
                    Create account
                  </Link>
                </div>
                <p className="muted small">
                  Use your email and password. You choose what gets shared with
                  neighbors and the fictional receiving agency.
                </p>
              </div>
            </AuthPage>
          }
        />
      </Routes>
    );
  return (
    <Routes>
      <Route path="/sign-in/*" element={<Navigate to="/" replace />} />
      <Route path="/sign-up/*" element={<Navigate to="/" replace />} />
      <Route
        path="*"
        element={
          <SessionWorkspace
            key={`${userId}:${sessionId}`}
            health={health}
            getToken={() => getToken()}
            onDraftKey={rememberDraft}
            signOut={() => signOut({ redirectUrl: '/' })}
          >
            {children}
          </SessionWorkspace>
        }
      />
    </Routes>
  );
}
function AuthPage({ children }: { children: ReactNode }) {
  return (
    <main className="auth-page">
      <Link to="/" className="brand">
        <span className="brand-symbol" aria-hidden="true" />
        <span>Neighborhood Fixer</span>
      </Link>
      <div className="auth-content">{children}</div>
      <p className="muted small">
        Demo Borough · Shared neighborhood, fictional agency
      </p>
    </main>
  );
}
