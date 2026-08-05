import { describe, expect, it } from "vitest";
import {
  buildTransportHeadline,
  comparisonPosition,
  displayScore,
  ratingForScore,
} from "./public-transport";
import type { ModelRollupData } from "./api";

function rollup(scores: Record<string, number | null>, overall = 80): ModelRollupData {
  return {
    overall_score: overall,
    display_score: overall,
    unrounded_score: overall,
    is_complete: true,
    counts_toward_recommendation: true,
    coverage_ratio: 1,
    assessed_components: Object.keys(scores),
    excluded_components: [],
    warnings: [],
    components: Object.entries(scores).map(([name, score]) => ({
      name,
      value: {},
      score,
      weight: 0.25,
      status: score == null ? "not_assessed" : "calculated",
      explanation: "test",
      strengths: [],
      limitations: [],
      evidence: [],
      source: "test",
      provenance: "CALCULATED",
      confidence: "high",
    })),
  };
}

describe("public transport presentation", () => {
  it.each([
    [100, "Excellent"], [85, "Excellent"], [84.99, "Good"], [70, "Good"],
    [69.99, "Fair"], [55, "Fair"], [54.99, "Limited"], [40, "Limited"],
    [39.99, "Very limited"], [0, "Very limited"], [null, null],
  ])("maps %s to %s", (score, expected) => {
    expect(ratingForScore(score)).toBe(expected);
  });

  it("rounds for display without changing the raw score", () => {
    expect(displayScore(86.7)).toEqual({ rawScore: 86.7, roundedScore: 87, rating: "Excellent" });
  });

  it("explains strong network reach with weaker access", () => {
    expect(buildTransportHeadline(rollup({ access: 68, bus_coverage: 94, mrt_reach: 93, route_resilience: 100 }, 87)))
      .toBe("Excellent bus and MRT reach, but only fair first-mile access.");
  });

  it("handles incomplete components without inventing a score", () => {
    expect(buildTransportHeadline(rollup({ access: null, bus_coverage: 80, mrt_reach: null, route_resilience: 60 }, 70)))
      .toBe("Strong bus connectivity, although rail access and MRT reach are less convenient.");
  });

  it("uses tolerance-aware joint positions", () => {
    expect(comparisonPosition(86.7, [86.7, 86.74, 70])).toBe("Joint 1st of 3");
    expect(comparisonPosition(null, [86, null])).toBe("Not available");
  });
});
