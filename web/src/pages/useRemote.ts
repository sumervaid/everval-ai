import { useCallback, useEffect, useState } from 'react';
import { request } from '../api';
import { errorMessage } from '../format';

export function useRemote<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError('');
    if (!path) return;
    setBusy(true);
    void request<T>(path, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(errorMessage(err));
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [path, revision]);
  return { data, error, busy, reload };
}
