import { create } from "zustand";
import type { RunStats, RunStatus } from "../lib/apiClient";

/**
 * Session-local runs list. There is no list-runs endpoint in the v1 contract,
 * so the BOQ tab's "build from run" picker offers runs started in THIS session
 * (and says so honestly).
 */
export interface RunRow {
  id: string;
  status: RunStatus;
  stats: RunStats | null;
  error: string | null;
  created_at: string;
}

interface RunStore {
  runs: RunRow[];
  addRun: (run: RunRow) => void;
  updateRun: (id: string, patch: Partial<Omit<RunRow, "id">>) => void;
  clear: () => void;
}

export const useRunStore = create<RunStore>((set) => ({
  runs: [],
  addRun: (run) => set((s) => ({ runs: [run, ...s.runs.filter((r) => r.id !== run.id)] })),
  updateRun: (id, patch) =>
    set((s) => ({ runs: s.runs.map((r) => (r.id === id ? { ...r, ...patch } : r)) })),
  clear: () => set({ runs: [] }),
}));
