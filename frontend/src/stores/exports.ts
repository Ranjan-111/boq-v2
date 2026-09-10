import { create } from "zustand";
import type { ExportStatus } from "../lib/apiClient";

/**
 * Session-local export history. There is no list-exports endpoint in the v1
 * contract, so the Exports tab shows exports created in THIS session and says
 * so honestly — full history arrives with the T104 artifacts view.
 */
export interface ExportRow {
  id: string;
  boq_id: string;
  status: ExportStatus;
  sha256: string | null;
  download_url: string | null;
  created_at: string;
}

interface ExportStore {
  exports: ExportRow[];
  addExport: (row: ExportRow) => void;
  updateExport: (id: string, patch: Partial<Omit<ExportRow, "id">>) => void;
  clear: () => void;
}

export const useExportStore = create<ExportStore>((set) => ({
  exports: [],
  addExport: (row) =>
    set((s) => ({ exports: [row, ...s.exports.filter((e) => e.id !== row.id)] })),
  updateExport: (id, patch) =>
    set((s) => ({
      exports: s.exports.map((e) => (e.id === id ? { ...e, ...patch } : e)),
    })),
  clear: () => set({ exports: [] }),
}));
