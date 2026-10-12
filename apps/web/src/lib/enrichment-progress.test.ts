import { describe, expect, it } from "vitest";
import { deriveEnrichmentProgress, expectedStageTypes } from "./enrichment-progress";
import type { EnrichmentRunStatus } from "@/lib/api";

describe("deriveEnrichmentProgress", () => {
  it("starts at zero without confirmed backend stages", () => {
    expect(deriveEnrichmentProgress({ jobStatus: "queued", progressStage: "queued", attempts: 0, runs: [] })).toMatchObject({
      percent: 0,
      completedCount: 0,
      message: "Starting enrichment…",
    });
  });

  it("counts settled checks per listing and describes active work", () => {
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

    // (8 + 8 + 22) of 2 listings x 100 weight; only geocoding is settled for both listings.
    expect(progress).toMatchObject({ percent: 19, completedCount: 1 });
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

  it("advances past unavailable or failed checks while still reporting them", () => {
    const progress = deriveEnrichmentProgress({
      jobStatus: "running",
      progressStage: "checking_schools",
      attempts: 1,
      runs: [
        { listing_id: "one", enrichment_type: "SCHOOLS", status: "UNAVAILABLE" },
        { listing_id: "one", enrichment_type: "FAIR_PRICE", status: "FAILED" },
      ],
    });

    expect(progress.percent).toBe(32);
    expect(progress.completedCount).toBe(2);
    expect(progress.unavailableCount).toBe(1);
    expect(progress.errorMessage).toBe("Fair price information is unavailable.");
  });

  it("never moves backwards as listings are enriched one after another", () => {
    // Mirrors the API: every stage for listing one, then every stage for listing two.
    const order = ["GEOCODING", "PROPERTY_DATA", "TRANSACTION_DATA", "LEASE", "FAIR_PRICE", "PUBLIC_TRANSPORT", "DRIVING_ACCESS"];
    const options = {
      listingIds: ["one", "two"],
      expectedTypes: expectedStageTypes({ main_transport_mode: "BOTH", schools_matter: false }),
    };
    const runs: EnrichmentRunStatus[] = [];
    const percents: number[] = [];
    const record = () =>
      percents.push(deriveEnrichmentProgress({ jobStatus: "running", progressStage: "calculating_transport", attempts: 1, runs: [...runs] }, options).percent);
    for (const listing_id of ["one", "two"]) {
      for (const enrichment_type of order) {
        runs.push({ listing_id, enrichment_type, status: "RUNNING" });
        record();
        // Listing two's fair price is unavailable, as for a listing without nearby sales.
        const status = listing_id === "two" && enrichment_type === "FAIR_PRICE" ? "UNAVAILABLE" : "SUCCEEDED";
        runs[runs.length - 1] = { listing_id, enrichment_type, status };
        record();
      }
    }

    expect(percents.every((value, index) => index === 0 || value >= percents[index - 1])).toBe(true);
    expect(percents.at(-1)).toBe(99);
  });

  it("leaves unrequested checks out of the total", () => {
    const expectedTypes = expectedStageTypes({ main_transport_mode: "MAINLY_PUBLIC_TRANSPORT", schools_matter: false });
    expect(expectedTypes).not.toContain("DRIVING_ACCESS");
    expect(expectedTypes).not.toContain("SCHOOLS");

    const runs = expectedTypes.map((enrichment_type) => ({ listing_id: "one", enrichment_type, status: "SUCCEEDED" }));
    const progress = deriveEnrichmentProgress(
      { jobStatus: "running", progressStage: "finalising_results", attempts: 1, runs },
      { listingIds: ["one"], expectedTypes },
    );

    expect(progress).toMatchObject({ percent: 99, completedCount: expectedTypes.length, totalChecks: expectedTypes.length });
  });
});
