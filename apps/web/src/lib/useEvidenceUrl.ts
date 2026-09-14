import { useEffect, useState } from 'react';
import { apiUrl, getAccessToken } from './auth';
/** AWS evidence is fetched with the access token; bearer values never go into URLs. */
export function useEvidenceUrl(source?: string) {
  const token = getAccessToken();
  const privateApi = source?.startsWith('/api/');
  const [resolved, setResolved] = useState<string>();
  useEffect(() => {
    if (!source || !privateApi || !token) {
      setResolved(undefined);
      return;
    }
    const controller = new AbortController();
    let objectUrl: string | undefined;
    fetch(apiUrl(source), {
      headers: { Authorization: `Bearer ${token}` },
      credentials: 'omit',
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok)
          throw new Error('Evidence permission or download failed');
        return response.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setResolved(objectUrl);
      })
      .catch(() => setResolved(undefined));
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [source, privateApi, token]);
  return !source
    ? undefined
    : privateApi && token
      ? resolved
      : privateApi
        ? apiUrl(source)
        : source;
}
