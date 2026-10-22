import { useCallback, useEffect, useRef, useState } from "react";

export type Loadable<T> = {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  reload: () => void;
};

/** Run `load` on mount and whenever `deps` change. Keeps the previous data while reloading. */
export function useApi<T>(load: () => Promise<T>, deps: unknown[] = []): Loadable<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadRef
      .current()
      .then((value) => {
        if (!cancelled) {
          setData(value);
          setError(undefined);
        }
      })
      .catch((err: Error) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

/** Wrap an async action with pending and error state for buttons and forms. */
export function useAction<A extends unknown[], R>(action: (...args: A) => Promise<R>) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const run = useCallback(
    async (...args: A): Promise<R | undefined> => {
      setPending(true);
      setError(undefined);
      try {
        return await action(...args);
      } catch (err) {
        setError((err as Error).message);
        return undefined;
      } finally {
        setPending(false);
      }
    },
    [action],
  );
  return { run, pending, error, setError };
}
