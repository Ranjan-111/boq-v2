import { useParams } from "react-router-dom";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../lib/apiClient";
import AppHeader from "../components/AppHeader";

type Tab = "drawings" | "runs" | "boq" | "exports";

const TABS: { id: Tab; label: string; note: string }[] = [
  { id: "drawings", label: "Drawings", note: "Upload arrives in Round 3." },
  { id: "runs", label: "Runs", note: "Measurement runs arrive in Round 3." },
  { id: "boq", label: "BOQ", note: "The BOQ workspace arrives in Round 4." },
  { id: "exports", label: "Exports", note: "Exports arrive in Round 4." },
];

export default function ProjectWorkspacePage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [tab, setTab] = useState<Tab>("drawings");
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: Boolean(projectId),
  });

  return (
    <div className="min-h-full">
      <AppHeader />
      <main className="mx-auto max-w-6xl px-6 py-8">
        {project.isPending ? (
          <div className="card h-16 animate-pulse bg-ink-100" />
        ) : project.isError ? (
          <div className="card p-8 text-center text-sm text-red-700">
            {project.error instanceof ApiError
              ? project.error.message
              : "Could not load this project."}
          </div>
        ) : project.data ? (
          <>
            <div className="mb-6">
              <h1 className="text-xl font-semibold text-ink-900">{project.data.name}</h1>
              <p className="mt-1 flex items-center gap-2 text-sm text-ink-500">
                <span>{project.data.client_name ?? "—"}</span>
                <span className="text-ink-300">·</span>
                <span className="rounded bg-ink-100 px-1.5 py-0.5 text-xs">
                  {project.data.region_code}
                </span>
                <span className="rounded bg-ink-100 px-1.5 py-0.5 text-xs">
                  {project.data.currency}
                </span>
              </p>
            </div>

            <div className="mb-4 flex gap-1 border-b border-ink-200">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={
                    "px-4 py-2 text-sm font-medium transition-colors " +
                    (tab === t.id
                      ? "border-b-2 border-accent-600 text-accent-700"
                      : "border-b-2 border-transparent text-ink-500 hover:text-ink-800")
                  }
                >
                  {t.label}
                </button>
              ))}
            </div>

            <div className="card p-8">
              {tab === "drawings" ? (
                <div className="grid place-items-center rounded-lg border-2 border-dashed border-ink-200 bg-ink-50 p-10 text-center">
                  <p className="text-sm font-medium text-ink-700">
                    Drawing upload arrives in Round 3
                  </p>
                  <p className="mt-1 max-w-sm text-xs text-ink-500">
                    PDF, DXF and raster drawings will upload here, parse to
                    sheets, and require scale confirmation before any
                    measurement runs.
                  </p>
                </div>
              ) : (
                <p className="text-center text-sm text-ink-500">
                  {TABS.find((t) => t.id === tab)?.note}
                </p>
              )}
            </div>
          </>
        ) : null}
      </main>
    </div>
  );
}
