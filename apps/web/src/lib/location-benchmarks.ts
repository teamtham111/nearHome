import type { BenchmarkComparison, BenchmarkMeasurement, ComparisonResponse, LocationBenchmarks } from "@/lib/api";

/** Component name -> the measurement that represents it in comparisons. */
export const COMPONENT_MEASUREMENT: Record<string, string> = {
  access: "pt_access_cost_minutes",
  bus_coverage: "pt_direct_bus_corridors",
  mrt_reach: "pt_mrt_stations_within_30_min",
  route_resilience: "pt_independent_fallbacks",
  major_road_access: "drive_major_road_baseline_minutes",
  // route_connectivity, peak_access_penalty and parking_convenience are
  // evidence only: they are deliberately never ranked.
};

export function benchmarksEnabled(data: Pick<ComparisonResponse, "location_benchmark_display">): boolean {
  return data.location_benchmark_display === "on";
}

export function measurementFor(benchmarks: LocationBenchmarks | undefined, key: string | undefined): BenchmarkMeasurement | null {
  if (!benchmarks || !key) return null;
  return benchmarks.measurements.find((measurement) => measurement.key === key) ?? null;
}

export function measurementsForFeature(benchmarks: LocationBenchmarks | undefined, featureSet: BenchmarkMeasurement["feature_set"]): BenchmarkMeasurement[] {
  return benchmarks?.measurements.filter((measurement) => measurement.feature_set === featureSet) ?? [];
}

export function titleCaseTown(name: string | null | undefined): string | null {
  if (!name) return null;
  return name
    .toLowerCase()
    .split(/(\s+|\/)/)
    .map((part) => (part.trim() && part !== "/" ? part[0].toUpperCase() + part.slice(1) : part))
    .join("");
}

function formatNumber(value: number): string {
  return Number.isInteger(value) ? value.toLocaleString("en-SG") : value.toLocaleString("en-SG", { maximumFractionDigits: 1 });
}

/** The raw measurement a comparison is based on, in plain words. */
export function formatMeasurementValue(measurement: BenchmarkMeasurement): string {
  if (measurement.value_status === "worst") {
    if (measurement.key === "pt_access_cost_minutes") return "No practical bus or rail entry confirmed";
    return "No practical option confirmed";
  }
  if (measurement.value_status !== "measured" || measurement.value == null) return "Not available";
  const value = measurement.value;
  switch (measurement.unit) {
    case "km":
      return value < 1 ? `${Math.round(value * 1000)} m` : `${formatNumber(Math.round(value * 100) / 100)} km`;
    case "minutes":
    case "generalised minutes":
      return `${formatNumber(Math.round(value * 10) / 10)} min`;
    default:
      return `${formatNumber(value)} ${value === 1 ? measurement.unit.replace(/s$/, "") : measurement.unit}`;
  }
}

function placeLabel(scope: "national" | "town", comparison: BenchmarkComparison): string | null {
  if (scope === "national") return "Singapore";
  return titleCaseTown(comparison.town_name);
}

/**
 * One comparison line, e.g. "Better than 42% of HDB blocks in Singapore".
 * Percentages arrive floored from the API; this never rounds them up.
 */
export function comparisonSentence(measurement: BenchmarkMeasurement, scope: "national" | "town"): string {
  const comparison = scope === "national" ? measurement.national : measurement.town;
  const place = placeLabel(scope, comparison);
  const where = place ? `in ${place}` : "in its town";
  if (!comparison || comparison.status === "unavailable" || measurement.value_status === "unavailable") {
    return scope === "national" ? "Singapore comparison not available" : "Town comparison not available";
  }
  if (comparison.status === "suppressed") {
    return `${scope === "national" ? "Singapore" : place ?? "Town"} comparison withheld — not enough blocks have data yet`;
  }
  const pct = comparison.better_than_pct ?? 0;
  const worse = comparison.strictly_worse ?? 0;
  const groupSize = comparison.group_size;
  const phrase = measurement.comparison_phrase || "Better than";
  if (worse === 0) return `None of the ${groupSize.toLocaleString("en-SG")} compared HDB blocks ${where} had a worse result`;
  if (worse === groupSize) return `${phrase} every other compared HDB block ${where}`;
  if (pct === 0) return `${phrase} fewer than 1% of HDB blocks ${where}`;
  return `${phrase} ${pct}% of HDB blocks ${where}`;
}

export function tieNote(comparison: BenchmarkComparison | undefined): string | null {
  if (!comparison || comparison.status !== "available" || !comparison.tied) return null;
  return `${comparison.tied.toLocaleString("en-SG")} other block${comparison.tied === 1 ? " has" : "s have"} the same result`;
}

export function coverageNote(measurement: BenchmarkMeasurement): string | null {
  if (measurement.complete_national_benchmark || measurement.national_coverage == null) return null;
  const pct = Math.floor(measurement.national_coverage * 100);
  return `Based on ${pct < 1 ? "under 1" : pct}% of Singapore's HDB residential blocks measured so far.`;
}
