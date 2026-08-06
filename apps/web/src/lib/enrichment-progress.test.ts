import { describe, expect, it } from "vitest";
import { deriveEnrichmentProgress } from "./enrichment-progress";

describe("deriveEnrichmentProgress", () => {
  it("starts at zero without confirmed backend stages", () => {
    expect(deriveEnrichmentProgress({ jobStatus: "queued", progressStage: "queued", attempts: 0, runs: [] })).toMatchObject({
      percent: 0,
      completedCount: 0,
      message: "Starting enrichment…",
    });
  });

  it("weights only fully successful stages and describes active work", () => {
    const progress = deriveEnrichmentProgress({
      jobStatus: "running",
      progressStage: "calculating_transport",
      attempts: 1,
      runs: [
        { listing_id: "one", enrichment_type: "GEOCODING", status: "SUCCEEDED" },
        { listing_id: "two", enrichment_type: "GEOCODING", status: "SUCCEEDED" },
        { listing_id: "one", enrichment_type: "FAIR_PRICE", status: "SUCCEEDED" },
        { listing_id: "two", enrichment_type: "FAIR_PRICE", status: "RUNNING" },
        { listing_id: "one", enrichment_type: "PUBLIC_TRANSPORT", status: "RUNNING" },
      ],
    });

    expect(progress).toMatchObject({ percent: 8, completedCount: 1 });
    expect(progress.message).toBe("Calculating fair-price estimates and public transport…");
  });

  it("only reaches 100 percent after a completed job", () => {
    const runs = [
      "GEOCODING", "PROPERTY_DATA", "LEASE", "TRANSACTION_DATA", "SCHOOLS", "FAIR_PRICE", "PUBLIC_TRANSPORT", "DRIVING_ACCESS",
    ].map((enrichment_type) => ({ listing_id: "one", enrichment_type, status: "SUCCEEDED" }));

    expect(deriveEnrichmentProgress({ jobStatus: "running", progressStage: "finalising_results", attempts: 1, runs }).percent).toBe(99);
    expect(deriveEnrichmentProgress({ jobStatus: "completed", progressStage: "completed", attempts: 1, runs })).toMatchObject({
      percent: 100,
      successful: true,
    });
  });

  it("does not treat unavailable or failed checks as completed", () => {
    const progress = deriveEnrichmentProgress({
      jobStatus: "running",
      progressStage: "checking_schools",
      attempts: 1,
      runs: [
        { listing_id: "one", enrichment_type: "SCHOOLS", status: "UNAVAILABLE" },
        { listing_id: "one", enrichment_type: "FAIR_PRICE", status: "FAILED" },
      ],
    });

    expect(progress.completedCount).toBe(0);
    expect(progress.unavailableCount).toBe(1);
    expect(progress.errorMessage).toBe("Fair price information is unavailable.");
  });
});
