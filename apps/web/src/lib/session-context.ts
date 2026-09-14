import { createContext, useContext } from 'react';
import type { Health, Session } from './api';
export const SessionContext = createContext<{
  session: Session;
  health: Health;
  switchResident: (resident: string) => Promise<void>;
} | null>(null);
export function useSession() {
  const value = useContext(SessionContext);
  if (!value) throw new Error('Session unavailable');
  return value;
}
