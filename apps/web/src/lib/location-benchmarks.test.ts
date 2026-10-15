import { describe, expect, it } from "vitest";

import type { BenchmarkMeasurement } from "@/lib/api";
import { COMPONENT_MEASUREMENT, comparisonSentence, coverageNote, formatMeasurementValue, tieNote, titleCaseTown } from "@/lib/location-benchmarks";

function measurement(overrides: Partial<BenchmarkMeasurement> = {}): BenchmarkMeasurement {
  return {
    key: "pt_direct_bus_corridors",
    feature_set: "public_transport",
    component: "bus_coverage",
    label: "Distinct direct bus corridors",
    unit: "corridors",
    direction: "higher",
    comparison_phrase: "Better than",
    value: 6,
    value_status: "measured",
    national: { status: "available", better_than_pct: 42, strictly_worse: 420, tied: 30, group_size: 1000 },
    town: { status: "available", better_than_pct: 61, strictly_worse: 61, tied: 0, group_size: 100, town_name: "KALLANG/WHAMPOA" },
    national_coverage: 1,
    complete_national_benchmark: true,
    ...overrides,
  };
}

describe("location benchmark copy", () => {
  it("uses the Singapore comparison first and the town second", () => {
    expect(comparisonSentence(measurement(), "national")).toBe("Better than 42% of HDB blocks in Singapore");
    expect(comparisonSentence(measurement(), "town")).toBe("Better than 61% of HDB blocks in Kallang/Whampoa");
  });

  it("never says percentile or out of 100", () => {
    const text = [comparisonSentence(measurement(), "national"), comparisonSentence(measurement(), "town")].join(" ");
    expect(text).not.toMatch(/percentile|\/100|out of 100|top/i);
  });

  it("uses neutral copy for bottom-ranked results", () => {
    const bottom = measurement({ national: { status: "available", better_than_pct: 0, strictly_worse: 0, tied: 12, group_size: 1000 } });
    expect(comparisonSentence(bottom, "national")).toBe("None of the 1,000 compared HDB blocks in Singapore had a worse result");
  });

  it("does not show 0% when some blocks are worse", () => {
    const nearBottom = measurement({ national: { status: "available", better_than_pct: 0, strictly_worse: 3, tied: 0, group_size: 1000 } });
    expect(comparisonSentence(nearBottom, "national")).toBe("Better than fewer than 1% of HDB blocks in Singapore");
  });

  it("only says every other block when literally true", () => {
    const top = measurement({ national: { status: "available", better_than_pct: 100, strictly_worse: 1000, tied: 0, group_size: 1000 } });
    expect(comparisonSentence(top, "national")).toBe("Better than every other compared HDB block in Singapore");
  });

  it("explains withheld and unavailable comparisons", () => {
    const suppressed = measurement({ town: { status: "suppressed", better_than_pct: null, strictly_worse: null, tied: null, group_size: 4, town_name: "BEDOK" } });
    expect(comparisonSentence(suppressed, "town")).toBe("Bedok comparison withheld — not enough blocks have data yet");
    const missing = measurement({ value: null, value_status: "unavailable", national: { status: "unavailable", better_than_pct: null, strictly_worse: null, tied: null, group_size: 0 } });
    expect(comparisonSentence(missing, "national")).toBe("Singapore comparison not available");
    expect(formatMeasurementValue(missing)).toBe("Not available");
  });

  it("never ranks route flexibility, peak-hour delay or parking", () => {
    expect(Object.keys(COMPONENT_MEASUREMENT).sort()).toEqual(["access", "bus_coverage", "major_road_access", "mrt_reach", "route_resilience"]);
    expect(COMPONENT_MEASUREMENT.major_road_access).toBe("drive_major_road_baseline_minutes");
  });

  it("shows ties, raw values and partial coverage", () => {
    expect(tieNote(measurement().national)).toBe("30 other blocks have the same result");
    expect(formatMeasurementValue(measurement())).toBe("6 corridors");
    expect(formatMeasurementValue(measurement({ key: "drive_major_road_baseline_minutes", unit: "minutes", value: 6.25 }))).toBe("6.3 min");
    expect(formatMeasurementValue(measurement({ key: "pt_access_cost_minutes", value: null, value_status: "worst" }))).toBe("No practical bus or rail entry confirmed");
    expect(coverageNote(measurement({ complete_national_benchmark: false, national_coverage: 0.637 }))).toBe(
      "Based on 63% of Singapore's HDB residential blocks measured so far.",
    );
    expect(titleCaseTown("CHOA CHU KANG")).toBe("Choa Chu Kang");
  });
});
