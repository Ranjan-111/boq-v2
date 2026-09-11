import { useParams } from "react-router-dom";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../lib/apiClient";
import AppHeader from "../components/AppHeader";
import DrawingsTab from "../components/DrawingsTab";
import RunsTab from "../components/RunsTab";
import BoqTab from "../components/BoqTab";
import ExportsTab from "../components/ExportsTab";
import AuditTab from "../components/AuditTab";
import CatalogTab from "../components/CatalogTab";

type Tab = "drawings" | "runs" | "boq" | "catalog" | "exports" | "audit";

const TABS: { id: Tab; label: string }[] = [
  { id: "drawings", label: "Drawings" },
  { id: "runs", label: "Runs" },
  { id: "boq", label: "BOQ" },
  { id: "catalog", label: "Catalogue" },
  { id: "exports", label: "Exports" },
  { id: "audit", label: "Audit" },
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

            {projectId && tab === "drawings" ? (
              <DrawingsTab projectId={projectId} />
            ) : projectId && tab === "runs" ? (
              <RunsTab projectId={projectId} />
            ) : projectId && tab === "boq" ? (
              <BoqTab projectId={projectId} />
            ) : projectId && tab === "catalog" && project.data ? (
              <CatalogTab
                regionCode={project.data.region_code}
                currency={project.data.currency}
              />
            ) : projectId && tab === "audit" ? (
              <AuditTab projectId={projectId} />
            ) : projectId ? (
              <ExportsTab projectId={projectId} />
            ) : null}
          </>
        ) : null}
      </main>
    </div>
  );
}
