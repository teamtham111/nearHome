import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitForEnrichmentJob } from "./wait-for-enrichment";

vi.mock("@/lib/api", () => ({
  getEnrichmentJob: vi.fn(),
}));

import { getEnrichmentJob } from "@/lib/api";

describe("waitForEnrichmentJob", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns once enrichment runs finish", async () => {
    vi.mocked(getEnrichmentJob)
      .mockResolvedValueOnce({ status: "running", progress_stage: "starting" } as never)
      .mockResolvedValueOnce({ status: "completed", progress_stage: "completed" } as never);

    await waitForEnrichmentJob("session-1", "job-1", { initialIntervalMs: 1, timeoutMs: 1_000 });

    expect(getEnrichmentJob).toHaveBeenCalledTimes(2);
  });

  it("does not treat a stale prior success as the current run", async () => {
    vi.mocked(getEnrichmentJob)
      .mockResolvedValueOnce({ status: "queued", progress_stage: "queued" } as never)
      .mockResolvedValueOnce({ status: "completed", progress_stage: "completed" } as never);

    await waitForEnrichmentJob("session-2", "job-2", { initialIntervalMs: 1, timeoutMs: 1_000 });

    expect(getEnrichmentJob).toHaveBeenCalledTimes(2);
  });

  it("reports a failed current run instead of resolving successfully", async () => {
    vi.mocked(getEnrichmentJob).mockResolvedValueOnce({
      status: "failed", progress_stage: "failed", error_message: "Provider unavailable",
    } as never);

    await expect(
      waitForEnrichmentJob("session-3", "job-3", { initialIntervalMs: 1, timeoutMs: 1_000 }),
    ).rejects.toThrow("Provider unavailable");
  });
});
