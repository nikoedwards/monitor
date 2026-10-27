import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useMutationState, type UseMutationResult } from "@tanstack/react-query";

export const PERSISTENT_TASK_EVENT = "monitor:persistent-task";

type StoredTask<T> = {
  status?: "pending" | "success" | "error";
  data?: T;
  error?: string;
  updatedAt: number;
};

function read<T>(storageKey: string): StoredTask<T> | null {
  try {
    const raw = localStorage.getItem(storageKey);
    return raw ? JSON.parse(raw) as StoredTask<T> : null;
  } catch {
    return null;
  }
}

function write<T>(storageKey: string, value: StoredTask<T>) {
  try {
    localStorage.setItem(storageKey, JSON.stringify(value));
  } catch {
    // The in-memory mutation/query cache remains available when storage is full.
  }
  window.dispatchEvent(new CustomEvent(PERSISTENT_TASK_EVENT, { detail: { storageKey } }));
}

export function persistentTaskKey(scope: string, parts: unknown[]): string {
  return `monitor.persistent-task.${scope}.${encodeURIComponent(JSON.stringify(parts))}`;
}

export function dailyPersistentTaskKey(scope: string, parts: unknown[]): string {
  const now = new Date();
  const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  return `${persistentTaskKey(scope, parts)}.${day}`;
}

export function persistTaskValue<T>(storageKey: string, data: T) {
  write(storageKey, { status: "success", data, updatedAt: Date.now() });
}

export function persistTaskPending(storageKey: string) {
  write(storageKey, { status: "pending", updatedAt: Date.now() });
}

export function persistTaskError(storageKey: string, error: unknown) {
  write<unknown>(storageKey, {
    status: "error",
    error: error instanceof Error ? error.message : String(error),
    updatedAt: Date.now(),
  });
}

export function readPersistentTask<T>(storageKey: string): StoredTask<T> | null {
  return read<T>(storageKey);
}

export function usePersistentTaskMutation<TData, TVariables>(
  storageKey: string,
  mutationFn: (variables: TVariables) => Promise<TData>,
): UseMutationResult<TData, Error, TVariables> & { data: TData | undefined; error: Error | null; isPending: boolean; isError: boolean } {
  const [stored, setStored] = useState<StoredTask<TData> | null>(() => read<TData>(storageKey));
  const mutationKeyRef = useRef(storageKey);
  const mutationMatchesKey = mutationKeyRef.current === storageKey;
  if (!mutationMatchesKey) mutationKeyRef.current = storageKey;
  const mutation = useMutation<TData, Error, TVariables>({
    mutationKey: [storageKey],
    gcTime: 24 * 60 * 60 * 1000,
    mutationFn: async (variables) => {
      write<TData>(storageKey, { status: "pending", updatedAt: Date.now() });
      try {
        const result = await mutationFn(variables);
        write(storageKey, { status: "success", data: result, updatedAt: Date.now() });
        return result;
      } catch (error) {
        write<TData>(storageKey, { status: "error", error: error instanceof Error ? error.message : String(error), updatedAt: Date.now() });
        throw error;
      }
    },
  });
  const cachedMutations = useMutationState({
    filters: { mutationKey: [storageKey] },
    select: (item) => ({
      status: item.state.status,
      data: item.state.data as TData | undefined,
      error: item.state.error as Error | null,
      submittedAt: item.state.submittedAt,
    }),
  });
  const cachedMutation = cachedMutations.reduce<typeof cachedMutations[number] | undefined>(
    (latest, item) => !latest || item.submittedAt > latest.submittedAt ? item : latest,
    undefined,
  );

  useEffect(() => {
    setStored(read<TData>(storageKey));
    mutation.reset();
    const onChange = (event: Event) => {
      const detail = (event as CustomEvent<{ storageKey?: string }>).detail;
      if (detail?.storageKey === storageKey) setStored(read<TData>(storageKey));
    };
    window.addEventListener(PERSISTENT_TASK_EVENT, onChange);
    return () => window.removeEventListener(PERSISTENT_TASK_EVENT, onChange);
  }, [storageKey]);

  const result = useMemo(() => {
    const data = stored?.status === "success" ? stored.data : mutationMatchesKey ? mutation.data : cachedMutation?.data;
    const error = (mutationMatchesKey ? mutation.error : null) || cachedMutation?.error || (stored?.status === "error" && stored.error ? new Error(stored.error) : null);
    const storedPending = stored?.status === "pending" && Date.now() - stored.updatedAt < 30 * 60_000;
    return {
      data,
      error,
      isPending: (mutationMatchesKey && mutation.isPending) || cachedMutation?.status === "pending" || storedPending,
      isError: Boolean(error),
    };
  }, [cachedMutation, mutation.data, mutation.error, mutation.isPending, mutationMatchesKey, stored]);

  return Object.assign(mutation, result);
}
