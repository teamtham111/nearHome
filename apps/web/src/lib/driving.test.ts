import { describe, expect, it } from "vitest";
import type { ModelRollupData } from "./api";
import {
  availabilitySnapshot,
  drivingStatus,
  parkingFacts,
  ratingForDrivingScore,
  routeResult,
} from "./driving";

const baseComponents = [
  ["major_road_access", 85, 0.3],
  ["route_connectivity", 89, 0.25],
  ["peak_access_penalty", 95, 0.25],
  ["parking_convenience", 85, 0.2],
] as const;

function rollup(overrides: Partial<ModelRollupData> = {}, missing: string[] = []): ModelRollupData {
  const components = baseComponents.filter(([name]) => !missing.includes(name)).map(([name, score, weight]) => ({
    name,
    value: name === "route_connectivity" ? { distinct_expressways_reached: 1, independent_alternatives: 1, partially_independent_alternatives: 0 } : {},
    score,
    weight,
    status: "calculated" as const,
    explanation: "test",
    strengths: [],
    limitations: [],
    evidence: [],
    source: "test",
    provenance: "CALCULATED",
    confidence: "high",
  }));
  return {
    overall_score: 86.4,
    display_score: 86.4,
    unrounded_score: 86.4,
    is_complete: missing.length === 0,
    counts_toward_recommendation: true,
    coverage_ratio: 1,
    assessed_components: components.map((component) => component.name),
    excluded_components: missing,
    warnings: [],
    components,
    ...overrides,
  };
}

describe("driving presentation", () => {
  it("uses the shared buyer-facing score bands", () => {
    expect(ratingForDrivingScore(85)).toBe("Excellent");
    expect(ratingForDrivingScore(84.99)).toBe("Good");
    expect(ratingForDrivingScore(70)).toBe("Good");
    expect(ratingForDrivingScore(55)).toBe("Fair");
    expect(ratingForDrivingScore(40)).toBe("Limited");
    expect(ratingForDrivingScore(39.99)).toBe("Very limited");
  });

  it("marks all assessed components as complete", () => {
    expect(drivingStatus(rollup())).toBe("Complete");
  });

  it("does not make the general result provisional when no destination is supplied", () => {
    expect(drivingStatus(rollup())).toBe("Complete");
  });

  it("marks a rollup without usable component evidence as unavailable", () => {
    expect(drivingStatus({ ...rollup(), display_score: null, unrounded_score: null })).toBe("Unavailable");
  });

  it("does not present below-threshold coverage as a provisional score", () => {
    expect(drivingStatus(rollup({ counts_toward_recommendation: false, coverage_ratio: 0.4 }))).toBe("Unavailable");
  });

  it("uses singular and plural route wording", () => {
    const component = rollup().components.find((item) => item.name === "route_connectivity")!;
    expect(routeResult(component)).toContain("1 major road or expressway");
    expect(routeResult({ ...component, value: { distinct_expressways_reached: 3, independent_alternatives: 2, partially_independent_alternatives: 1 } })).toContain("3 major roads or expressways");
    expect(routeResult({ ...component, value: { distinct_expressways_reached: 1, independent_alternatives: 1, partially_independent_alternatives: 1 } })).toContain("1 independent alternative route and 1 partially independent alternative route");
  });

  it("classifies fresh, delayed, stale and malformed availability deterministically", () => {
    const now = new Date("2026-08-03T14:00:00Z");
    const availability = { available_lots: 73, total_lots: 265, updated_at: "2026-08-03T13:58:00Z" };
    expect(availabilitySnapshot(availability, now).label).toBe("Live snapshot");
    expect(availabilitySnapshot({ ...availability, updated_at: "2026-08-03T13:45:00Z" }, now).label).toBe("Updated 15 minutes ago");
    expect(availabilitySnapshot({ ...availability, updated_at: "2026-08-03T11:00:00Z" }, now).label).toBe("Stale data");
    expect(availabilitySnapshot({ ...availability, updated_at: "not-a-date" }, now).label).toBe("Current availability unavailable");
    expect(availabilitySnapshot({ ...availability, timestamp_valid: false }, now).label).toBe("Current availability unavailable");
    expect(availabilitySnapshot({ available_lots: null, total_lots: 265, updated_at: availability.updated_at }, now).state).toBe("unavailable");
  });

  it("formats availability in Singapore time", () => {
    const result = availabilitySnapshot({ available_lots: 73, total_lots: 265, updated_at: "2026-08-03T13:49:00Z" }, new Date("2026-08-03T13:50:00Z"));
    expect(result.reportedAt).toBe("3 August 2026, 9:49 PM");
  });

  it("omits unknown parking features", () => {
    const component = {
      ...rollup().components.find((item) => item.name === "parking_convenience")!,
      value: { primary_carpark: { address: "BLK 1", walk_minutes: 1, sheltered_status: "UNKNOWN", night_parking: "NO", parking_system_type: "ELECTRONIC" }, reasonable_carparks_within_500m: 5 },
    };
    const facts = parkingFacts(component);
    expect(facts).toContain("Electronic parking");
    expect(facts).toContain("Night parking unavailable");
    expect(facts.join(" ")).not.toContain("UNKNOWN");
  });
});
