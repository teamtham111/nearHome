import type { ComponentResultData, ModelRollupData } from "./api";

export type DrivingRating = "Excellent" | "Good" | "Fair" | "Limited" | "Very limited";
export type DrivingScoreDisplay = {
  rawScore: number | null;
  roundedScore: number | null;
  rating: DrivingRating | null;
};

export const DRIVING_SCORE_TIE_TOLERANCE = 0.05;
export const DRIVING_COMPONENT_NAMES = [
  "major_road_access",
  "route_connectivity",
  "peak_access_penalty",
  "parking_convenience",
] as const;

export function ratingForDrivingScore(score: number | null | undefined): DrivingRating | null {
  if (score == null || !Number.isFinite(score)) return null;
  if (score >= 85) return "Excellent";
  if (score >= 70) return "Good";
  if (score >= 55) return "Fair";
  if (score >= 40) return "Limited";
  return "Very limited";
}

export function displayDrivingScore(score: number | null | undefined): DrivingScoreDisplay {
  const rawScore = score == null || !Number.isFinite(score) ? null : score;
  return {
    rawScore,
    roundedScore: rawScore == null ? null : Math.round(rawScore),
    rating: ratingForDrivingScore(rawScore),
  };
}

export function formatDrivingScore(score: DrivingScoreDisplay): string {
  return score.roundedScore == null ? "Not assessed" : `${score.roundedScore}/100`;
}

export function drivingComponent(rollup: ModelRollupData | undefined, name: string): ComponentResultData | null {
  return rollup?.components?.find((component) => component.name === name) ?? null;
}

export function drivingStatus(rollup: ModelRollupData | undefined): "Complete" | "Provisional" | "Unavailable" {
  if (!rollup || rollup.display_score == null || !rollup.components?.some((component) => component.score != null)) {
    return "Unavailable";
  }
  if (!rollup.counts_toward_recommendation) return "Unavailable";
  const missingComponent = DRIVING_COMPONENT_NAMES.some((name) => !drivingComponent(rollup, name));
  return rollup.is_complete && !missingComponent ? "Complete" : "Provisional";
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function selectedRoadEvidence(component: ComponentResultData | null): Record<string, unknown> {
  return component?.evidence.find((entry) => entry.selected === true) ?? component?.evidence[0] ?? {};
}

function destinationEvidence(component: ComponentResultData | null): Array<Record<string, unknown>> {
  const value = record(component?.value);
  const destinations = Array.isArray(value.destinations) ? value.destinations : component?.evidence ?? [];
  return destinations.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item));
}

export function drivingHeadline(rollup: ModelRollupData): string {
  const status = drivingStatus(rollup);
  if (status === "Unavailable") return "Driving connectivity could not be assessed from the available evidence.";
  const road = displayDrivingScore(drivingComponent(rollup, "major_road_access")?.score).rating;
  const routes = displayDrivingScore(drivingComponent(rollup, "route_connectivity")?.score).rating;
  const parking = displayDrivingScore(drivingComponent(rollup, "parking_convenience")?.score).rating;
  if (road === "Excellent" && routes && ["Excellent", "Good"].includes(routes) && parking && ["Excellent", "Good"].includes(parking)) {
    return "Strong general driving connectivity, with several route options and convenient nearby parking.";
  }
  if (routes === "Excellent" || routes === "Good") {
    return "Several route options are available, although some parts of the driving assessment are less convenient.";
  }
  if (road === "Excellent" || road === "Good") {
    return "Convenient access to the major-road network, although route flexibility or parking is more limited.";
  }
  return "Driving connectivity is available, but reaching and using the wider road network may be less convenient.";
}

export function drivingExplanation(rollup: ModelRollupData): string {
  const status = drivingStatus(rollup);
  if (status === "Unavailable") return "There is not enough confirmed routing or parking evidence to calculate a meaningful driving result.";
  return "This is a general neighbourhood driving result based on major-road access, route flexibility, peak-hour access reliability and parking convenience. It does not depend on a personal destination.";
}

export function roadResult(component: ComponentResultData | null): string | null {
  const evidence = selectedRoadEvidence(component);
  const name = typeof evidence.name === "string" ? evidence.name : null;
  const expressway = typeof evidence.expressway === "string" ? evidence.expressway : null;
  const minutes = finiteNumber(evidence.peak_duration_minutes ?? record(component?.value).peak_duration_minutes);
  if (!name && !expressway && minutes == null) return null;
  const routeName = name && expressway ? `${expressway} via ${name}` : name ?? expressway ?? "The selected major-road entry";
  return `${routeName} is approximately ${Math.round(minutes ?? 0)} minutes away during morning peak hours.`;
}

export function destinationResult(component: ComponentResultData | null): string | null {
  const destinations = destinationEvidence(component);
  if (!destinations.length) return null;
  const value = record(component?.value);
  const average = finiteNumber(value.average_duration_minutes);
  const labels = destinations.map((item) => typeof item.label === "string" ? item.label : null).filter(Boolean) as string[];
  if (average == null) return null;
  const destinationText = labels.length === 1 ? `to ${labels[0]}` : labels.length > 1 ? `to ${labels.slice(0, 2).join(" and ")}` : "to your regular destination";
  return `The morning-peak journey ${destinationText} is approximately ${Math.round(average)} minutes.`;
}

export function routeResult(component: ComponentResultData | null): string | null {
  const value = record(component?.value);
  const roads = finiteNumber(value.distinct_expressways_reached);
  const independent = finiteNumber(value.independent_alternatives);
  const partial = finiteNumber(value.partially_independent_alternatives);
  if (roads == null && independent == null && partial == null) return null;
  const roadText = roads == null ? "the assessed road network" : `${roads} major road${roads === 1 ? " or expressway" : "s or expressways"}`;
  const partialText = partial == null ? null : `${partial} partially independent alternative route${partial === 1 ? "" : "s"}`;
  const independentText = independent == null ? null : `${independent} independent alternative route${independent === 1 ? "" : "s"}`;
  const alternatives = [independentText, partialText].filter(Boolean).join(" and ");
  return `The assessed routes reach ${roadText}${alternatives ? `, with ${alternatives}` : ""}.`;
}

export function peakResult(component: ComponentResultData | null): string | null {
  const value = record(component?.value);
  const evidence = selectedRoadEvidence(component);
  const peak = finiteNumber(evidence.peak_duration_minutes);
  const offPeak = finiteNumber(evidence.off_peak_duration_minutes);
  const penalty = finiteNumber(value.penalty_minutes ?? evidence.penalty_minutes);
  if (peak == null && offPeak == null && penalty == null) return null;
  return `Morning peak: ${peak == null ? "Not available" : `${Math.round(peak)} min`} · Off-peak: ${offPeak == null ? "Not available" : `${Math.round(offPeak)} min`} · Additional peak delay: ${penalty == null ? "Not available" : `approximately ${penalty.toFixed(1)} min`}.`;
}

export function parkingPrimary(component: ComponentResultData | null): Record<string, unknown> | null {
  const primary = record(record(component?.value).primary_carpark);
  return Object.keys(primary).length ? primary : null;
}

function friendlyValue(value: unknown): string | null {
  if (value == null || value === "" || String(value).toUpperCase() === "UNKNOWN") return null;
  return String(value).replace(/_/g, " ").toLowerCase();
}

export function parkingFacts(component: ComponentResultData | null): string[] {
  const primary = parkingPrimary(component);
  const value = record(component?.value);
  if (!primary) return [];
  const facts: string[] = [];
  const walk = finiteNumber(primary.walk_minutes);
  if (walk != null) facts.push(`Approximately ${Math.max(1, Math.round(walk))}-minute walk`);
  const type = friendlyValue(primary.carpark_type);
  if (type) facts.push(type.replace(/\b\w/g, (letter) => letter.toUpperCase()));
  const sheltered = String(primary.sheltered_status ?? "").toUpperCase();
  if (sheltered === "YES") facts.push("Sheltered");
  if (sheltered === "NO") facts.push("Not sheltered");
  const electronic = friendlyValue(primary.parking_system_type);
  if (electronic?.includes("electronic")) facts.push("Electronic parking");
  const night = String(primary.night_parking ?? "").toUpperCase();
  if (night === "YES") facts.push("Night parking available");
  if (night === "NO") facts.push("Night parking unavailable");
  const alternatives = finiteNumber(value.reasonable_carparks_within_500m);
  if (alternatives != null) facts.push(`${alternatives} nearby carpark option${alternatives === 1 ? "" : "s"} within 500 metres`);
  return facts;
}

export type AvailabilitySnapshot = {
  state: "live" | "updated" | "delayed" | "stale" | "unavailable";
  label: string;
  countText?: string;
  reportedAt?: string;
  explanation: string;
};

export function availabilitySnapshot(availability: Record<string, unknown> | null, now = new Date()): AvailabilitySnapshot {
  const available = finiteNumber(availability?.available_lots);
  const total = finiteNumber(availability?.total_lots);
  const timestamp = typeof availability?.updated_at === "string" ? new Date(availability.updated_at) : null;
  if (!availability || availability.timestamp_valid === false || available == null || total == null || total <= 0 || !timestamp || !Number.isFinite(timestamp.getTime())) {
    return { state: "unavailable", label: "Current availability unavailable", explanation: "A valid official availability count and report time were not available." };
  }
  const ageMinutes = Math.max(0, (now.getTime() - timestamp.getTime()) / 60000);
  const countText = `${Math.round(available)} of ${Math.round(total)} car lots reported available`;
  const reportedAt = formatSingaporeTimestamp(timestamp);
  if (ageMinutes <= 5) return { state: "live", label: "Live snapshot", countText, reportedAt, explanation: "This is a point-in-time availability report. It may no longer reflect current conditions and does not show how difficult parking is normally." };
  if (ageMinutes <= 15) return { state: "updated", label: `Updated ${Math.max(1, Math.round(ageMinutes))} minutes ago`, countText, reportedAt, explanation: "This is a point-in-time availability report. It may no longer reflect current conditions and does not show how difficult parking is normally." };
  if (ageMinutes <= 120) return { state: "delayed", label: "Delayed data", countText, reportedAt, explanation: "The latest report is delayed and may no longer reflect current conditions." };
  return { state: "stale", label: "Stale data", countText, reportedAt, explanation: "Current availability cannot be confirmed from this older point-in-time report." };
}

function formatSingaporeTimestamp(date: Date): string {
  const parts = new Intl.DateTimeFormat("en-SG", {
    timeZone: "Asia/Singapore", day: "numeric", month: "long", year: "numeric", hour: "numeric", minute: "2-digit", hour12: true,
  }).formatToParts(date);
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("day")} ${get("month")} ${get("year")}, ${get("hour")}:${get("minute")} ${get("dayPeriod").toUpperCase()}`;
}

export function provisionalComponents(rollup: ModelRollupData): ComponentResultData[] {
  return rollup.components.filter((component) => component.score == null);
}

export function unavailableDrivingComponentNames(rollup: ModelRollupData): string[] {
  return DRIVING_COMPONENT_NAMES.filter((name) => !drivingComponent(rollup, name) || drivingComponent(rollup, name)?.score == null);
}

export function compareDrivingScores(scores: Array<number | null>): string {
  const available = scores.filter((score): score is number => score != null && Number.isFinite(score));
  if (!available.length) return "No driving score can currently be compared.";
  const best = Math.max(...available);
  const bestCount = available.filter((score) => Math.abs(score - best) <= DRIVING_SCORE_TIE_TOLERANCE).length;
  return bestCount > 1 ? "The shortlisted homes have a similar driving result." : "The listing with the highest score currently performs better for general driving connectivity.";
}
