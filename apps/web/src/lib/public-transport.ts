import type { ComponentResultData, ModelRollupData } from "@/lib/api";

export type RatingLabel = "Excellent" | "Good" | "Fair" | "Limited" | "Very limited";

export interface DisplayScore {
  rawScore: number | null;
  roundedScore: number | null;
  rating: RatingLabel | null;
}

export const SCORE_TIE_TOLERANCE = 0.05;

export function ratingForScore(score: number | null | undefined): RatingLabel | null {
  if (score == null || !Number.isFinite(score)) return null;
  if (score >= 85) return "Excellent";
  if (score >= 70) return "Good";
  if (score >= 55) return "Fair";
  if (score >= 40) return "Limited";
  return "Very limited";
}

export function displayScore(score: number | null | undefined): DisplayScore {
  const rawScore = score == null || !Number.isFinite(score) ? null : score;
  return {
    rawScore,
    roundedScore: rawScore == null ? null : Math.round(rawScore),
    rating: ratingForScore(rawScore),
  };
}

export function formatDisplayScore(score: DisplayScore): string {
  return score.roundedScore == null ? "Not available" : `${score.roundedScore}/100`;
}

export function componentByName(
  rollup: ModelRollupData | null | undefined,
  name: string,
): ComponentResultData | null {
  return rollup?.components?.find((component) => component.name === name) ?? null;
}

export function isUsableScore(score: number | null | undefined): score is number {
  return score != null && Number.isFinite(score);
}

function scoreOf(component: ComponentResultData | null): number | null {
  return component && component.status !== "not_assessed" && component.status !== "provider_error" && component.status !== "insufficient_data"
    ? component.score
    : null;
}

export function buildTransportHeadline(rollup: ModelRollupData): string {
  const overall = displayScore(rollup.display_score);
  const access = scoreOf(componentByName(rollup, "access"));
  const bus = scoreOf(componentByName(rollup, "bus_coverage"));
  const mrt = scoreOf(componentByName(rollup, "mrt_reach"));
  const overallLabel = overall.rating ?? "Available";
  const networkStrong = [bus, mrt].every((score) => isUsableScore(score) && score >= 70);
  const networkWeak = [bus, mrt].every((score) => isUsableScore(score) && score < 70);

  if (networkStrong && isUsableScore(access) && Math.min(bus ?? access, mrt ?? access) - access >= 12) {
    return `${overallLabel} bus and MRT reach, but only ${ratingForScore(access)?.toLowerCase() ?? "limited"} first-mile access.`;
  }
  if (isUsableScore(access) && access >= 70 && networkWeak) {
    return "Convenient access to public transport, but the available network coverage is more limited.";
  }
  if (isUsableScore(bus) && bus >= 70 && (!isUsableScore(mrt) || mrt < 70)) {
    return "Strong bus connectivity, although rail access and MRT reach are less convenient.";
  }
  if (isUsableScore(mrt) && mrt >= 70 && (!isUsableScore(bus) || bus < 70)) {
    return "Strong MRT connectivity, although nearby bus coverage is more limited.";
  }
  if (isUsableScore(access) && access >= 70 && isUsableScore(bus) && bus >= 70 && isUsableScore(mrt) && mrt >= 70) {
    return `${overallLabel} access and broad bus and MRT connectivity.`;
  }
  return `${overallLabel} public transport connectivity with ${ratingForScore(access)?.toLowerCase() ?? "unavailable"} network access.`;
}

export function buildTransportExplanation(rollup: ModelRollupData): string {
  const access = scoreOf(componentByName(rollup, "access"));
  const bus = scoreOf(componentByName(rollup, "bus_coverage"));
  const mrt = scoreOf(componentByName(rollup, "mrt_reach"));
  if (isUsableScore(access) && isUsableScore(bus) && isUsableScore(mrt) && access + 12 < Math.min(bus, mrt)) {
    return "Once you enter the transport network, this home provides broad bus and rail connectivity. However, reaching the first useful bus stop or MRT station is less convenient.";
  }
  if (isUsableScore(access) && access >= 70 && isUsableScore(bus) && bus >= 70 && isUsableScore(mrt) && mrt >= 70) {
    return "The home is convenient to reach and offers broad bus and rail connectivity across the network.";
  }
  if (isUsableScore(access) && access >= 70) {
    return "The first part of the journey is convenient, although the wider network has more limited coverage.";
  }
  return "This result reflects the confirmed routes and network structure available around the home.";
}

export function buildTransportTradeoff(rollup: ModelRollupData): string | null {
  const access = scoreOf(componentByName(rollup, "access"));
  const bus = scoreOf(componentByName(rollup, "bus_coverage"));
  const mrt = scoreOf(componentByName(rollup, "mrt_reach"));
  if (isUsableScore(access) && isUsableScore(bus) && isUsableScore(mrt) && access + 12 < Math.min(bus, mrt)) {
    return "Strong connectivity after entering the network, but a less convenient first part of the journey.";
  }
  if (isUsableScore(bus) && bus >= 70 && (!isUsableScore(mrt) || mrt < 70)) {
    return "Bus coverage is the strength, while rail reach is the main compromise.";
  }
  if (isUsableScore(mrt) && mrt >= 70 && (!isUsableScore(bus) || bus < 70)) {
    return "Rail reach is the strength, while nearby bus coverage is the main compromise.";
  }
  if (isUsableScore(access) && access >= 70 && isUsableScore(bus) && bus >= 70 && isUsableScore(mrt) && mrt >= 70) {
    return null;
  }
  return "The result reflects the balance between first-mile access and network reach.";
}

export function buildBestFor(rollup: ModelRollupData): string | null {
  const access = componentByName(rollup, "access");
  const selected = (access?.value as Record<string, unknown> | null)?.selected_access_path;
  if (!selected || typeof selected !== "object") return null;
  const path = selected as Record<string, unknown>;
  const total = typeof path.total_expected_minutes === "number" ? Math.round(path.total_expected_minutes) : null;
  const walk = typeof path.walk_minutes === "number" ? Math.round(path.walk_minutes) : null;
  const bus = scoreOf(componentByName(rollup, "bus_coverage"));
  const mrt = scoreOf(componentByName(rollup, "mrt_reach"));
  if (total == null || (!isUsableScore(bus) && !isUsableScore(mrt))) return null;
  const entry = path.access_mode === "feeder_bus"
    ? "a feeder connection"
    : walk == null
      ? `an approximately ${total}-minute trip to enter the network`
      : `an approximately ${walk}-minute walk to enter the network`;
  return `Buyers who value broad transport coverage and do not mind ${entry}.`;
}

export function comparisonPosition(
  score: number | null | undefined,
  scores: Array<number | null | undefined>,
): string {
  if (!isUsableScore(score)) return "Not available";
  const usable = scores.filter(isUsableScore).sort((left, right) => right - left);
  const groups: number[] = [];
  for (const value of usable) {
    if (!groups.some((group) => Math.abs(group - value) <= SCORE_TIE_TOLERANCE)) groups.push(value);
  }
  const rank = groups.findIndex((group) => Math.abs(group - score) <= SCORE_TIE_TOLERANCE) + 1;
  const tied = usable.filter((value) => Math.abs(value - score) <= SCORE_TIE_TOLERANCE).length > 1;
  return `${tied ? "Joint " : ""}${rank}${rank === 1 ? "st" : rank === 2 ? "nd" : rank === 3 ? "rd" : "th"} of ${scores.length}`;
}

export function transferCoverageLabel(count: number | null): string | null {
  if (count == null || !Number.isFinite(count)) return null;
  if (count <= 0) return "Limited additional coverage";
  if (count <= 3) return "Some additional coverage";
  if (count <= 7) return "Broad additional coverage";
  return "Very broad additional coverage";
}

export function lineName(code: string): string {
  const names: Record<string, string> = {
    NSL: "North–South Line",
    EWL: "East–West Line",
    NEL: "North East Line",
    CCL: "Circle Line",
    CCL_BRANCH: "Circle Line",
    CCL_LOOP: "Circle Line",
    DTL: "Downtown Line",
    TEL: "Thomson–East Coast Line",
    JSL: "Jurong Region Line",
    CRL: "Cross Island Line",
  };
  return names[code] ?? code;
}

export function methodologyReliability(rollup: ModelRollupData): "High" | "Medium" | "Low" | "Unavailable" {
  const assessed = rollup.components.filter((component) => component.score != null);
  if (!assessed.length) return "Unavailable";
  if (assessed.some((component) => component.confidence === "low" || component.status === "provider_error")) return "Low";
  if (assessed.some((component) => component.confidence !== "high")) return "Medium";
  return "High";
}
