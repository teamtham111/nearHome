import { getEnrichmentJob } from "@/lib/api";

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Poll one durable enrichment job with bounded exponential backoff. */
export async function waitForEnrichmentJob(
  sessionId: string,
  jobId: string,
  options?: { timeoutMs?: number; initialIntervalMs?: number; maxIntervalMs?: number },
) {
  const timeoutMs = options?.timeoutMs ?? 15 * 60_000;
  const initialIntervalMs = options?.initialIntervalMs ?? 1_000;
  const maxIntervalMs = options?.maxIntervalMs ?? 8_000;
  const deadline = Date.now() + timeoutMs;
  let intervalMs = initialIntervalMs;
  let lastTransientError: Error | null = null;

  while (Date.now() < deadline) {
    let job;
    try {
      job = await getEnrichmentJob(sessionId, jobId);
      lastTransientError = null;
    } catch (error) {
      // A temporary polling failure must not turn a healthy queued worker job
      // into a visible enrichment failure. Real terminal state is authoritative.
      lastTransientError = error instanceof Error ? error : new Error("Status temporarily unavailable");
    }

    if (job?.status === "completed") return job;
    if (job && ["failed", "cancelled"].includes(job.status)) {
      throw new Error(job.error_message ?? "Enrichment could not be completed. Please retry.");
    }

    await sleep(intervalMs);
    intervalMs = Math.min(maxIntervalMs, Math.round(intervalMs * 1.6));
  }

  throw new Error(
    lastTransientError
      ? "We could not check enrichment progress. Please refresh in a moment."
      : "Enrichment is taking longer than expected. Please refresh in a moment.",
  );
}
