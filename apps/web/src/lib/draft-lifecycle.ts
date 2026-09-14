import type { Session } from './api';
export function reportDraftKey(session: Session) {
  const generation = session.generation ? `:${session.generation}` : '';
  return `nf-report:${session.workspace_id}${generation}:${session.user.id}`;
}
/** Draft deletion follows verified auth transitions, never ordinary React unmounts. */
export function clearReportDraft(key?: string) {
  if (!key) return;
  try {
    localStorage.removeItem(key);
  } catch {
    // Storage may be unavailable. This must not prevent provider sign-out.
  }
}
export function createDraftLifecycle() {
  let current: { subject: string; key: string } | undefined;
  return {
    remember(subject: string, key: string) {
      if (current && (current.subject !== subject || current.key !== key))
        clearReportDraft(current.key);
      current = { subject, key };
    },
    observeIdentity(
      isLoaded: boolean,
      isSignedIn: boolean | undefined,
      subject: string | null | undefined,
    ) {
      if (!isLoaded || !current) return;
      if (!isSignedIn || current.subject !== subject) {
        clearReportDraft(current.key);
        current = undefined;
      }
    },
  };
}
export type DraftLifecycle = ReturnType<typeof createDraftLifecycle>;
