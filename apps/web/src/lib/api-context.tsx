import { createContext, useContext } from 'react';
import type { ApiClient } from './api';
export const ApiContext = createContext<ApiClient | null>(null);
export function useApi() {
  const api = useContext(ApiContext);
  if (!api) throw new Error('API session unavailable');
  return api;
}
