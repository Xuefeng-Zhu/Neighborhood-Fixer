import { useEffect, useState } from 'react';
import { useApi } from './api-context';
/** Private evidence uses the same fresh token and cancellation scope as JSON requests. */
export function useEvidenceUrl(source?: string) {
  const api = useApi();
  const privateApi = source?.startsWith('/api/');
  const [resolved, setResolved] = useState<{ source: string; url: string }>();
  useEffect(() => {
    setResolved(undefined);
    if (!source || !privateApi) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    api
      .evidence(source, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setResolved({ source, url: objectUrl });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResolved(undefined);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [source, privateApi, api]);
  return privateApi
    ? resolved && resolved.source === source
      ? resolved.url
      : undefined
    : source;
}
