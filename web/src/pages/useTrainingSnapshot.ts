import { useCallback, useEffect, useState } from 'react';
import { request } from '../api';
import { errorMessage } from '../format';

/** Keep the last same-run snapshot visible during refresh, never across run changes. */
export function useTrainingSnapshot<T>(path: string | null) {
  const [snapshot, setSnapshot] = useState<{ path: string; data: T } | null>(null);
  const [failure, setFailure] = useState<{ path: string; message: string } | null>(null);
  const [pendingPath, setPendingPath] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    if (!path) return;
    setPendingPath(path);
    void request<T>(path, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) {
          setSnapshot({ path, data });
          setFailure(null);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setFailure({ path, message: errorMessage(error) });
      })
      .finally(() => {
        if (!controller.signal.aborted) setPendingPath(null);
      });
    return () => controller.abort();
  }, [path, revision]);
  return {
    data: snapshot?.path === path ? snapshot.data : null,
    error: failure?.path === path ? failure.message : '',
    busy: !!path && pendingPath === path,
    reload,
  };
}
