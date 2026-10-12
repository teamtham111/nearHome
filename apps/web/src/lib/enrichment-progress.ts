import type { EnrichmentRunStatus } from "@/lib/api";

export type EnrichmentProgressSnapshot = {
  jobStatus: string;
  progressStage: string;
  attempts: number;
  errorMessage?: string | null;
  runs: EnrichmentRunStatus[];
};

export type StageState = "pending" | "running" | "succeeded" | "unavailable" | "failed" | "not_requested";

export type EnrichmentStage = {
  type: string;
  label: string;
  action: string;
  weight: number;
};

export const ENRICHMENT_STAGES: readonly EnrichmentStage[] = [
  { type: "GEOCODING", label: "Geocoding", action: "Geocoding listing addresses", weight: 8 },
  { type: "PROPERTY_DATA", label: "Property data", action: "Retrieving property data", weight: 6 },
  { type: "LEASE", label: "Lease data", action: "Retrieving lease data", weight: 8 },
  { type: "TRANSACTION_DATA", label: "Transaction data", action: "Retrieving transaction data", weight: 8 },
  { type: "SCHOOLS", label: "Schools", action: "Analysing nearby schools", weight: 10 },
  { type: "FAIR_PRICE", label: "Fair price", action: "Calculating fair-price estimates", weight: 22 },
  { type: "PUBLIC_TRANSPORT", label: "Public transport", action: "Calculating public transport access", weight: 22 },
  { type: "DRIVING_ACCESS", label: "Driving access", action: "Calculating driving access", weight: 16 },
];

const JOB_STAGE_TO_TYPES: Record<string, string[]> = {
  geocoding_and_property: ["GEOCODING", "PROPERTY_DATA", "LEASE"],
  calculating_fair_price: ["FAIR_PRICE"],
  calculating_transport: ["PUBLIC_TRANSPORT"],
  calculating_driving: ["DRIVING_ACCESS"],
  checking_schools: ["SCHOOLS"],
};

export type DerivedEnrichmentProgress = {
  percent: number;
  completedCount: number;
  unavailableCount: number;
  totalChecks: number;
  terminal: boolean;
  successful: boolean;
  message: string;
  errorMessage: string | null;
  stages: Array<EnrichmentStage & { state: StageState }>;
};

/** Statuses meaning a listing's check has finished, whatever its outcome. */
const SETTLED_STATUSES = new Set(["SUCCEEDED", "UNAVAILABLE", "FAILED", "ERROR", "SKIPPED"]);

export type EnrichmentProgressOptions = {
  /** Every listing in the job. Listings without status rows yet still count as outstanding work. */
  listingIds?: string[];
  /** Stage types the job will run (mirrors the API's plan_enrichments); others are left out of the total. */
  expectedTypes?: string[];
};

/** Stage types the API plans for a buyer profile; see EnrichmentService.plan_enrichments. */
export function expectedStageTypes(profile: { main_transport_mode: string; schools_matter: boolean } | null | undefined): string[] {
  const types = ["GEOCODING", "PROPERTY_DATA", "TRANSACTION_DATA", "LEASE", "FAIR_PRICE"];
  if (!profile) return types;
  if (profile.main_transport_mode === "MAINLY_PUBLIC_TRANSPORT" || profile.main_transport_mode === "BOTH") types.push("PUBLIC_TRANSPORT");
  if (profile.main_transport_mode === "MAINLY_DRIVING" || profile.main_transport_mode === "BOTH") types.push("DRIVING_ACCESS");
  if (profile.schools_matter) types.push("SCHOOLS");
  return types;
}

function stageState(type: string, runs: EnrichmentRunStatus[], terminal: boolean, listingIds: string[]): StageState {
  const typeRuns = runs.filter((run) => run.enrichment_type === type);
  const statuses = typeRuns.map((run) => run.status.toUpperCase());
  if (statuses.length === 0) return terminal ? "not_requested" : "pending";
  if (statuses.some((status) => status === "RUNNING" || status === "QUEUED")) return "running";
  // Listings are enriched one after another: a stage finished for the first
  // listing is still in progress until the remaining listings reach it.
  if (!terminal && listingIds.some((id) => !typeRuns.some((run) => run.listing_id === id))) return "running";
  if (statuses.every((status) => status === "SUCCEEDED")) return "succeeded";
  if (statuses.some((status) => status === "FAILED" || status === "ERROR")) return "failed";
  return "unavailable";
}

function joinActions(stages: EnrichmentStage[]): string {
  if (stages.length === 0) return "Starting enrichment";
  if (stages.length === 1) return stages[0].action;
  if (stages.length === 2) return `${stages[0].action} and ${stages[1].label.toLowerCase()}`;
  return `${stages.slice(0, 2).map((stage) => stage.label.toLowerCase()).join(" and ")} calculations`;
}

export function deriveEnrichmentProgress(
  snapshot: EnrichmentProgressSnapshot,
  options: EnrichmentProgressOptions = {},
): DerivedEnrichmentProgress {
  const terminal = ["completed", "failed", "cancelled"].includes(snapshot.jobStatus);
  const successful = snapshot.jobStatus === "completed";
  const listingIds = options.listingIds?.length
    ? options.listingIds
    : [...new Set(snapshot.runs.map((run) => run.listing_id))];
  const states = new Map(ENRICHMENT_STAGES.map((stage) => [stage.type, stageState(stage.type, snapshot.runs, terminal, listingIds)]));
  const unavailableCount = ENRICHMENT_STAGES.filter((stage) => {
    const state = states.get(stage.type);
    return state === "unavailable" || state === "not_requested";
  }).length;
  const failedStage = ENRICHMENT_STAGES.find((stage) => states.get(stage.type) === "failed");
  const runningStages = ENRICHMENT_STAGES.filter((stage) => states.get(stage.type) === "running");
  const fallbackStages = (JOB_STAGE_TO_TYPES[snapshot.progressStage] ?? [])
    .map((type) => ENRICHMENT_STAGES.find((stage) => stage.type === type))
    .filter((stage): stage is EnrichmentStage => Boolean(stage));
  // Progress is work done per listing: a check counts once it has settled for
  // that listing, even if its result is unavailable. Counting only stage-wide
  // successes made the bar fall back whenever a later listing started a stage
  // or returned an unavailable result.
  const expected = new Set(options.expectedTypes ?? ENRICHMENT_STAGES.map((stage) => stage.type));
  const plannedStages = ENRICHMENT_STAGES.filter((stage) => expected.has(stage.type));
  const settled = new Set(
    snapshot.runs
      .filter((run) => SETTLED_STATUSES.has(run.status.toUpperCase()))
      .map((run) => `${run.listing_id}|${run.enrichment_type}`),
  );
  const totalWeight = plannedStages.reduce((total, stage) => total + stage.weight, 0) * listingIds.length;
  const settledWeight = plannedStages.reduce(
    (total, stage) => total + stage.weight * listingIds.filter((id) => settled.has(`${id}|${stage.type}`)).length,
    0,
  );
  const rawPercent = totalWeight ? Math.floor((settledWeight / totalWeight) * 100) : 0;

  let message: string;
  if (successful) {
    message = "All available property, price and accessibility information is ready.";
  } else if (snapshot.jobStatus === "failed" || snapshot.jobStatus === "cancelled") {
    message = snapshot.errorMessage ?? "Enrichment could not be completed. Please try again.";
  } else if (snapshot.progressStage === "retrying" || snapshot.attempts > 1) {
    message = `Retrying ${joinActions(runningStages.length ? runningStages : fallbackStages).toLowerCase()}…`;
  } else if (failedStage) {
    message = `${failedStage.label} calculation could not be completed.`;
  } else if (snapshot.jobStatus === "queued") {
    message = "Starting enrichment…";
  } else {
    message = `${joinActions(runningStages.length ? runningStages : fallbackStages)}…`;
  }

  return {
    percent: successful ? 100 : Math.min(99, rawPercent),
    completedCount: plannedStages.filter((stage) => listingIds.length > 0 && listingIds.every((id) => settled.has(`${id}|${stage.type}`))).length,
    unavailableCount,
    totalChecks: plannedStages.length,
    terminal,
    successful,
    message,
    errorMessage: failedStage ? `${failedStage.label} information is unavailable.` : null,
    stages: ENRICHMENT_STAGES.map((stage) => ({ ...stage, state: states.get(stage.type) ?? "pending" })),
  };
}
