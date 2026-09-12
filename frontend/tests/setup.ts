import { vi } from "vitest";

/**
 * happy-dom (this vitest setup) exposes no localStorage global, and the auth
 * store reads it at module scope (src/stores/auth.ts) — so it must exist
 * before ANY test module imports any app module. Test files may re-stub it
 * with their own map (see apiClient.test.ts); this is only the baseline.
 */
const storage = new Map<string, string>();
vi.stubGlobal(
  "localStorage",
  {
    getItem: (k: string) => storage.get(k) ?? null,
    setItem: (k: string, v: string) => void storage.set(k, v),
    removeItem: (k: string) => void storage.delete(k),
    clear: () => void storage.clear(),
  } as Storage,
);

// React's act() needs this flag outside react's own test setup — silences the
// "not configured to support act(...)" warning from the .tsx component tests.
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
