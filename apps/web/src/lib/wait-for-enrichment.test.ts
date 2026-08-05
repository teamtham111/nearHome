import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitForEnrichmentComplete } from "./wait-for-enrichment";

vi.mock("@/lib/api", () => ({
  getEnrichmentStatus: vi.fn(),
}));

import { getEnrichmentStatus } from "@/lib/api";

describe("waitForEnrichmentComplete", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns once enrichment runs finish", async () => {
    vi.mocked(getEnrichmentStatus)
      .mockResolvedValueOnce({ runs: [{ enrichment_type: "GEOCODING", status: "RUNNING" }] })
      .mockResolvedValueOnce({ runs: [{ enrichment_type: "GEOCODING", status: "SUCCEEDED" }] });

    await waitForEnrichmentComplete("session-1", { intervalMs: 1, timeoutMs: 1_000 });

    expect(getEnrichmentStatus).toHaveBeenCalledTimes(2);
  });

  it("does not treat a stale prior success as the current run", async () => {
    vi.mocked(getEnrichmentStatus)
      .mockResolvedValueOnce({ runs: [{ enrichment_type: "FAIR_PRICE", status: "QUEUED" }] })
      .mockResolvedValueOnce({ runs: [{ enrichment_type: "FAIR_PRICE", status: "SUCCEEDED" }] });

    await waitForEnrichmentComplete("session-2", { intervalMs: 1, timeoutMs: 1_000 });

    expect(getEnrichmentStatus).toHaveBeenCalledTimes(2);
  });

  it("reports a failed current run instead of resolving successfully", async () => {
    vi.mocked(getEnrichmentStatus).mockResolvedValueOnce({
      runs: [{ enrichment_type: "FAIR_PRICE", status: "FAILED", error_message: "Provider unavailable" }],
    });

    await expect(
      waitForEnrichmentComplete("session-3", { intervalMs: 1, timeoutMs: 1_000 }),
    ).rejects.toThrow("Provider unavailable");
  });
});
