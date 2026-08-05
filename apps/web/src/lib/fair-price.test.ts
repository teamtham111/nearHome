import { describe, expect, it } from "vitest";

import type { FairPriceData, SessionListing } from "./api";
import {
  buildComparableReason,
  buildConfidenceExplanation,
  buildFilterSummary,
  buildValuationLimitations,
  comparableLeaseDisplay,
  displayedComparables,
  eligibleComparableCount,
  fairPriceModelDisplayName,
  formatValuationDifference,
  getComparableMatchLabel,
  getPriceAssessment,
} from "./fair-price";

const listing: SessionListing = {
  listing_id: "listing-1",
  display_name: "217 Bishan Street 23",
  address: "217 Bishan Street 23",
  asking_price: 850_000,
  floor_area_sqm: 103,
  flat_type: "4 ROOM",
  flat_model: "Model A",
  storey_range: null,
  remaining_lease_months: 777,
};

function fairPrice(overrides: Partial<FairPriceData> = {}): FairPriceData {
  return {
    central_estimate: 838_252,
    range_low: 790_145,
    range_high: 908_773,
    confidence: "MEDIUM",
    confidence_reasons: ["Comparable price spread is wide"],
    comparable_evidence: { eligible_comparable_count: 209, comparable_price_spread: 300_000 },
    filter_status: {
      town: { status: "applied" },
      flat_type: { status: "applied" },
      flat_model: { status: "applied" },
      area_band: { status: "applied" },
      lease_band: { status: "applied" },
      storey_range: { status: "omitted_missing" },
      relaxation_steps: [],
    },
    town: "BISHAN",
    flat_model_used: "Model A",
    remaining_lease_estimate: {
      display_value: "Estimated remaining lease: About 64 years 9 months",
      source: "hdb_same_block_transactions",
      confidence: "high",
      is_estimated: true,
      as_of_date: "2026-08-04",
    },
    ...overrides,
  };
}

describe("valuation difference", () => {
  it("describes asking price above the estimate using estimate denominator", () => {
    const difference = formatValuationDifference(850_000, 838_252);
    expect(difference.kind).toBe("above");
    expect(difference.amount).toBe(11_748);
    expect(difference.percentage).toBeCloseTo(1.4015, 4);
    expect(difference.label).toBe("S$11,748 above estimate · 1.4%");
  });

  it("handles below, equal, missing and invalid estimates", () => {
    expect(formatValuationDifference(830_000, 838_000).label).toBe("S$8,000 below estimate · 1.0%");
    expect(formatValuationDifference(838_000, 838_000).label).toBe("Matches estimate");
    expect(formatValuationDifference(null, 838_000).label).toBe("Asking price unavailable");
    expect(formatValuationDifference(838_000, 0).label).toBe("Asking price unavailable");
  });
});

describe("buyer-facing price assessment", () => {
  it.each([
    [2, "Close to estimated market value"],
    [2.01, "Slightly above estimate"],
    [5, "Slightly above estimate"],
    [5.01, "Above estimate"],
    [10, "Above estimate"],
    [10.01, "Significantly above estimate"],
    [-2.01, "Slightly below estimate"],
    [-2, "Close to estimated market value"],
    [-5, "Slightly below estimate"],
    [-5.01, "Below estimate"],
    [-10, "Below estimate"],
    [-10.01, "Significantly below estimate"],
  ])("maps %s%% to %s", (percentage, label) => {
    expect(getPriceAssessment(percentage)?.label).toBe(label);
  });
});

describe("buyer-facing valuation evidence", () => {
  it("summarises applied filters and missing storey without raw status objects", () => {
    const summary = buildFilterSummary(fairPrice(), listing);
    expect(summary).toContain("209 relevant transactions");
    expect(summary).toContain("same flat type");
    expect(summary).toContain("storey information was omitted");
    expect(summary).not.toContain("level_");
  });

  it("distinguishes a genuinely relaxed search from omitted missing fields", () => {
    const summary = buildFilterSummary(
      fairPrice({
        filter_status: {
          town: { status: "relaxed" },
          flat_type: { status: "applied" },
          area_band: { status: "relaxed" },
          lease_band: { status: "applied" },
          relaxation_steps: ["Floor-area tolerance widened during comparable selection."],
        },
      }),
      listing,
    );
    expect(summary).toContain("the search was widened");
    expect(summary).toContain("Floor-area tolerance widened");
    expect(summary).not.toContain("No wider search was required");
  });

  it("explains confidence from count, missing fields and spread", () => {
    const explanation = buildConfidenceExplanation(fairPrice());
    expect(explanation).toContain("209 relevant transactions");
    expect(explanation).toContain("storey information was missing");
    expect(explanation).toContain("varied widely");
  });

  it("creates limitations only from supported evidence", () => {
    const limitations = buildValuationLimitations(fairPrice(), listing);
    expect(limitations.map((item) => item.title)).toEqual([
      "Missing storey information",
      "Remaining lease is estimated",
      "Wide variation in nearby sale prices",
    ]);
  });

  it("handles missing lease and partial valuation responses without empty diagnostics", () => {
    const partial: FairPriceData = {
      confidence: "LOW",
      confidence_reasons: [],
      status: "AVAILABLE",
    };
    expect(buildConfidenceExplanation(partial)).toContain("limited or weakly matched");
    expect(buildFilterSummary(partial, undefined)).toContain("recent transactions");
    expect(buildValuationLimitations(partial, undefined).map((item) => item.title)).toEqual([
      "Missing storey information",
      "Flat model unavailable",
      "Remaining lease unavailable",
    ]);
  });

  it("uses confirmed comparable fields to explain relevance", () => {
    const reason = buildComparableReason(
      {
        block: "217",
        street: "Bishan Street 23",
        flat_type: "4 ROOM",
        flat_model: "Model A",
        floor_area_sqm: 102,
        remaining_lease_months: 780,
        transaction_date: "2026-06",
        age_months: 2,
        resale_price: 790_000,
        similarity: 0.21,
      },
      listing,
    );
    expect(reason).toContain("Same street");
    expect(reason).toContain("Same flat type");
    expect(reason).toContain("Same flat model");
    expect(reason).toContain("Sold recently");
  });

  it("maps the inspected similarity distribution to broad labels", () => {
    expect(getComparableMatchLabel(0.21)).toBe("Very similar");
    expect(getComparableMatchLabel(0.1)).toBe("Similar");
    expect(getComparableMatchLabel(0.05)).toBe("Moderately similar");
    expect(getComparableMatchLabel(0.02)).toBe("Contextual match");
    expect(getComparableMatchLabel(null)).toBe("Contextual match");
  });

  it("caps displayed comparables and orders ties by recency then ID", () => {
    const rows = Array.from({ length: 12 }, (_, index) => ({
      transaction_id: String(12 - index),
      transaction_date: index < 2 ? "2026-01" : `2025-${String(index + 1).padStart(2, "0")}`,
      similarity: index < 2 ? 0.2 : 0.1,
    }));
    const result = fairPrice({ displayed_comparables: rows, eligible_transaction_count: 4032 });
    expect(displayedComparables(result)).toHaveLength(10);
    expect(displayedComparables(result).slice(0, 2).map((row) => row.transaction_id)).toEqual(["11", "12"]);
    expect(eligibleComparableCount(result)).toBe(4032);
  });

  it("formats comparable lease, model wording and missing lease safely", () => {
    expect(comparableLeaseDisplay({ remaining_lease_months: 780 })).toBe("About 65 years");
    expect(comparableLeaseDisplay({})).toBe("Not available");
    expect(fairPriceModelDisplayName(fairPrice({ method: "CATBOOST" }))).toBe("CatBoost");
    expect(fairPriceModelDisplayName(fairPrice({ method: "UNKNOWN" }))).toBeNull();
  });
});
