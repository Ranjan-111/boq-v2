/**
 * Job/run polling — pure stop-conditions (unit-tested) plus thin TanStack
 * Query wrappers that use them as refetchInterval predicates.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api, type Job, type Run, type JobStatus, type RunStatus } from "./apiClient";

export const JOB_POLL_INTERVAL_MS = 1500;
export const RUN_POLL_INTERVAL_MS = 1500;

/** Terminal job statuses — polling stops once reached. */
export function isTerminalJobStatus(status: JobStatus | string): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

/** Whether a caller has an active job that should keep its action disabled.
 * Disabled queries report `isPending` even before a job id is supplied, so
 * the id is part of this guard rather than being inferred from query state. */
export function isJobActive(
  jobId: string | null,
  pending: boolean,
  status: JobStatus | string | undefined,
): boolean {
  return jobId !== null && (pending || status === "queued" || status === "running");
}

/** Terminal run statuses — polling stops once reached. */
export function isTerminalRunStatus(status: RunStatus | string): boolean {
  return (
    status === "completed" ||
    status === "completed_with_exceptions" ||
    status === "failed"
  );
}

/**
 * Poll-interval for a job: 1500ms while queued/running, `false` (stop) once
 * terminal. Undefined status (first fetch in flight) keeps polling.
 */
export function jobPollIntervalMs(status: JobStatus | undefined): number | false {
  return status !== undefined && isTerminalJobStatus(status)
    ? false
    : JOB_POLL_INTERVAL_MS;
}

/** Same for runs. */
export function runPollIntervalMs(status: RunStatus | undefined): number | false {
  return status !== undefined && isTerminalRunStatus(status)
    ? false
    : RUN_POLL_INTERVAL_MS;
}

/** Poll GET /jobs/{id} until it reaches a terminal status. */
export function useJobPoll(jobId: string | null): UseQueryResult<Job, Error> {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.getJob(jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) =>
      jobPollIntervalMs(query.state.data?.status as JobStatus | undefined),
  });
}

/** Poll GET /runs/{id} until it reaches a terminal status. */
export function useRunPoll(runId: string | null): UseQueryResult<Run, Error> {
  return useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId!),
    enabled: runId !== null,
    refetchInterval: (query) =>
      runPollIntervalMs(query.state.data?.status as RunStatus | undefined),
  });
}
