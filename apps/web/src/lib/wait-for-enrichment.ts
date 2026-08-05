import { getEnrichmentStatus } from "@/lib/api";

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Poll until queued background enrichment finishes. No-op for inline runs. */
export async function waitForEnrichmentComplete(
  sessionId: string,
  options?: { timeoutMs?: number; intervalMs?: number },
) {
  const timeoutMs = options?.timeoutMs ?? 120_000;
  const intervalMs = options?.intervalMs ?? 2_000;
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const { runs } = await getEnrichmentStatus(sessionId);
    const active = runs.some((run) => ["QUEUED", "RUNNING"].includes(run.status));
    // UNAVAILABLE is a terminal, user-visible partial result (for example a
    // provider may be disabled); only an actual FAILED run should reject the
    // whole enrichment action.
    const failures = runs.filter((run) => run.status === "FAILED");
    const terminal = runs.length > 0 && runs.every((run) => ["SUCCEEDED", "FAILED", "UNAVAILABLE"].includes(run.status));

    if (failures.length > 0 && !active) {
      const firstFailure = failures.find((run) => run.error_message);
      throw new Error(firstFailure?.error_message ?? "One or more enrichment steps failed. Please retry.");
    }
    if (!active && terminal) {
      return;
    }

    await sleep(intervalMs);
  }

  throw new Error("Enrichment is taking longer than expected. Please refresh in a moment.");
}
