type ApiFetchInit = RequestInit & { timeoutMs?: number };

// Keep ordinary interactions responsive, while allowing operations that deliberately
// perform retrieval or inline enrichment to complete without a premature abort.
export const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
export const LONG_RUNNING_REQUEST_TIMEOUT_MS = 120_000;

function getApiUrl(): string {
  const deploymentEnvironment = process.env.NEXT_PUBLIC_DEPLOYMENT_ENV;
  const configuredUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

  if (deploymentEnvironment === "production" && !configuredUrl) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL is required for the production web build");
  }

  // Preserve the original local variable during the migration, but never use
  // it as a production fallback. Public variables are inlined by Next at build
  // time, so the production deployment must provide the public HTTPS API URL.
  const rawUrl = configuredUrl ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    throw new Error("NEXT_PUBLIC_API_BASE_URL must be an absolute URL");
  }
  if (deploymentEnvironment === "production" && url.protocol !== "https:") {
    throw new Error("NEXT_PUBLIC_API_BASE_URL must use HTTPS in production");
  }
  return url.toString().replace(/\/$/, "");
}

const API_URL = getApiUrl();

export function parseApiError(detail: string): string {
  try {
    const parsed = JSON.parse(detail) as { detail?: unknown; message?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (typeof parsed.message === "string") return parsed.message;
    if (parsed.detail && typeof parsed.detail === "object" && "message" in parsed.detail) {
      const structured = parsed.detail as { message?: unknown; providerMessage?: unknown };
      const message = structured.message;
      const providerMessage = structured.providerMessage;
      if (typeof message === "string" && typeof providerMessage === "string" && providerMessage) {
        return `${message} (${providerMessage})`;
      }
      if (typeof message === "string") return message;
    }
    if (Array.isArray(parsed.detail)) {
      return parsed.detail.map((d) => String((d as { msg?: string }).msg ?? d)).join("; ");
    }
  } catch {
    /* plain text */
  }
  return detail || "Something went wrong";
}

export async function apiFetch<T>(path: string, init?: ApiFetchInit): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const timeoutMs = init?.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const abortFromCaller = () => controller.abort();
  init?.signal?.addEventListener("abort", abortFromCaller, { once: true });

  let res: Response;
  try {
    const { timeoutMs: _timeoutMs, ...requestInit } = init ?? {};
    res = await fetch(`${API_URL}${path}`, {
      ...requestInit,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });
  } catch (error) {
    if (timedOut) throw new Error("The analysis is taking longer than expected. Please retry in a moment.");
    if (error instanceof TypeError && process.env.NEXT_PUBLIC_DEPLOYMENT_ENV === "production") {
      throw new Error("The analysis service may be starting. Please wait a moment and retry.");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
    init?.signal?.removeEventListener("abort", abortFromCaller);
  }
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(parseApiError(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export interface SessionResponse {
  session_id: string;
  demo_mode: boolean;
  created_at: string;
}

export interface FairPriceComparable {
  transaction_id?: string | number;
  address?: string;
  transaction_date?: string;
  month?: string;
  block?: string;
  street?: string;
  town?: string;
  flat_type?: string;
  flat_model?: string | null;
  floor_area_sqm?: number | null;
  storey_range?: string | null;
  remaining_lease_months?: number | null;
  remaining_lease?: number | null;
  resale_price?: number | null;
  price_per_sqm?: number | null;
  age_months?: number | null;
  similarity?: number | null;
  similarity_components?: Record<string, number>;
}

export interface RemainingLeaseEstimateData {
  remaining_lease_months?: number | null;
  display_value?: string;
  lease_commencement_year?: number | null;
  source?: string;
  confidence?: string;
  is_estimated?: boolean;
  as_of_date?: string | null;
  warning?: string | null;
}

export interface FairPriceData {
  central_estimate?: number;
  final_estimate?: number;
  range_low?: number;
  range_high?: number;
  asking_difference_dollars?: number;
  asking_difference_pct?: number;
  value_gap_percentage?: number;
  confidence?: string;
  confidence_reasons?: string[];
  comparables?: FairPriceComparable[];
  displayed_comparables?: FairPriceComparable[];
  eligible_transaction_count?: number;
  warnings?: string[];
  filter_messages?: string[];
  warning_details?: Array<{ code: string; severity: string; message: string }>;
  filter_status?: Record<string, unknown>;
  comparable_count_by_stage?: Record<string, number>;
  comparable_evidence?: Record<string, unknown>;
  remaining_lease_years_used?: number | null;
  remaining_lease_months_used?: number | null;
  remaining_lease_source?: string;
  remaining_lease_confidence?: string;
  remaining_lease_as_of_date?: string | null;
  remaining_lease_estimate?: RemainingLeaseEstimateData;
  remaining_lease_status?: string;
  remaining_lease_provenance?: string;
  comparable_model_version?: string;
  town?: string | null;
  town_source?: string | null;
  flat_model_used?: string | null;
  flat_model_source?: string | null;
  status?: string;
  method?: string;
  model_version?: string;
}

export interface SessionListing {
  listing_id: string;
  display_name: string;
  address: string;
  asking_price: number;
  floor_area_sqm: number;
  flat_type: string;
  flat_type_raw?: string | null;
  listing_flat_subtype?: string | null;
  raw_listing_subtype?: string | null;
  flat_model?: string | null;
  flat_model_source?: string | null;
  subtype_conflicts?: Array<Record<string, unknown>>;
  storey_range?: string | null;
  lease_commencement_year?: number | null;
  remaining_lease_months?: number | null;
  remaining_lease_years?: number | null;
  remaining_lease_source?: string | null;
  remaining_lease_confidence?: string | null;
  remaining_lease_as_of_date?: string | null;
}

export interface ComponentResultData {
  name: string;
  value: unknown;
  score: number | null;
  weight: number;
  status: "calculated" | "estimated" | "partially_assessed" | "not_assessed" | "provider_error" | "insufficient_data";
  explanation: string;
  strengths: string[];
  limitations: string[];
  evidence: Array<Record<string, unknown>>;
  source: string | null;
  provenance: string;
  confidence: string;
}

export interface ModelRollupData {
  overall_score: number | null;
  display_score: number | null;
  unrounded_score?: number | null;
  is_complete: boolean;
  counts_toward_recommendation: boolean;
  coverage_ratio: number;
  assessed_components: string[];
  excluded_components: string[];
  warnings: string[];
  components: ComponentResultData[];
}

export interface ComparisonResponse {
  session_id: string;
  listing_count: number;
  can_compare: boolean;
  immediate_metrics: Array<{
    listing_id: string;
    metric_name: string;
    raw_value: unknown;
    unit: string | null;
    status: string;
    explanation: string;
    formula?: string | null;
    provenance: string;
  }>;
  preference_scores: Array<{
    listing_id: string;
    overall_fit_score?: number | null;
    total_score?: number | null;
    rank?: number | null;
    coverage?: number | null;
    sub_scores?: Record<string, number>;
  }>;
  recommendation: {
    recommended_listing_id: string | null;
    is_tie: boolean;
    is_provisional: boolean;
    one_sentence_summary: string;
    confidence: string;
    confidence_reasons: string[];
    why_not_selected: Record<string, string>;
    decision_hinge: string | null;
  } | null;
  fair_price_status: string;
  fair_price_by_listing: Record<string, FairPriceData>;
  transport_by_listing: Record<string, ModelRollupData>;
  driving_by_listing: Record<string, ModelRollupData>;
  schools_by_listing: Record<string, {
    score?: number | null;
    score_status?: "calculated" | "partial" | "missing_input" | "unavailable" | "error" | "not_applicable";
    missing_reasons?: string[];
    warnings?: string[];
    status?: string;
    schools_within_1km?: number;
    schools_within_2km?: number;
    nearest_school_distance_km?: number | null;
    named_school_distances_km?: Record<string, number | null>;
    matched_named_schools?: Record<string, string | null>;
    nearby_schools?: Array<{ school_name: string; level: string; distance_km: number; address: string }>;
    named_school_distance_km?: number | null;
    explanation?: string;
  }>;
  observations: Array<{
    observation_id: string;
    listing_id: string;
    category: string;
    value_text: string;
    verification_state: string;
  }>;
  journey_results: Array<{
    listing_id: string;
    important_location_id: string;
    mode: string;
    duration_minutes: number | null;
    difference_from_fastest_seconds: number | null;
    is_fastest: boolean | null;
    status: string;
    provider: string;
    requested_day_type: string;
    requested_time_local: string;
  }>;
  regular_destination_journeys: Array<{
    journey_estimate_id: string;
    listing_id: string;
    important_location_id: string;
    destination_label: string;
    destination_address?: string | null;
    selected_day_type: string;
    selected_time_local: string;
    duration_minutes: number | null;
    difference_from_fastest_seconds: number | null;
    is_fastest: boolean | null;
    status: string;
    provider: string;
    provider_status?: string | null;
    source: string;
    confidence: string;
  }>;
  enrichment_summary: Array<EnrichmentRunStatus>;
  requirement_results?: Array<Record<string, unknown>>;
  demo_mode: boolean;
}

export type EnrichmentRunStatus = {
    listing_id: string;
    enrichment_type: string;
    status: string;
    error_message?: string | null;
};

export function createSession() {
  return apiFetch<SessionResponse>("/api/v1/sessions", { method: "POST" });
}

export function getComparison(sessionId: string, signal?: AbortSignal) {
  return apiFetch<ComparisonResponse>(`/api/v1/sessions/${sessionId}/comparison`, { signal });
}

export function saveBuyerProfile(sessionId: string, body: unknown) {
  return apiFetch(`/api/v1/sessions/${sessionId}/buyer-profile`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function addManualListing(sessionId: string, body: unknown) {
  return apiFetch(`/api/v1/sessions/${sessionId}/listings/manual`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteListing(sessionId: string, listingId: string) {
  return apiFetch<void>(`/api/v1/sessions/${sessionId}/listings/${listingId}`, {
    method: "DELETE",
  });
}

export function getSession(sessionId: string) {
  return apiFetch<{
    session_id: string;
    profile_saved: boolean;
    buyer_profile: {
      max_budget: number;
      main_transport_mode: string;
      schools_matter: boolean;
      named_schools: string[];
      named_school: string | null;
      priorities: string[];
      important_locations?: Array<{
        important_location_id: string;
        label: string;
        place_id?: string | null;
        formatted_address?: string | null;
        latitude?: number | null;
        longitude?: number | null;
        usual_day_type?: "WEEKDAY" | "WEEKEND" | null;
        departure_time_local?: string | null;
        transport_mode?: string | null;
        is_complete?: boolean;
      }>;
    } | null;
    listings: SessionListing[];
    listing_count: number;
    demo_mode: boolean;
  }>(`/api/v1/sessions/${sessionId}`);
}

export function startEnrichment(sessionId: string) {
  return apiFetch<{
    job_id: string;
    status: "queued" | "running" | "completed" | "failed" | "cancelled";
    status_url: string;
  }>(
    `/api/v1/sessions/${sessionId}/enrichment/start`,
    { method: "POST", timeoutMs: LONG_RUNNING_REQUEST_TIMEOUT_MS },
  );
}

export type EnrichmentJobStatus = {
  job_id: string;
  session_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress_stage: string;
  attempts: number;
  error_code: string | null;
  error_message: string | null;
  result_available: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
};

export function getEnrichmentJob(sessionId: string, jobId: string) {
  const query = new URLSearchParams({ session_id: sessionId });
  return apiFetch<EnrichmentJobStatus>(`/api/v1/jobs/${jobId}?${query.toString()}`);
}

export function getEnrichmentStatus(sessionId: string) {
  return apiFetch<{
    runs: EnrichmentRunStatus[];
  }>(`/api/v1/sessions/${sessionId}/enrichment/status`);
}

export function smartPaste(
  sessionId: string,
  body: { sourceType: "url"; sourceUrl: string } | { sourceType: "text"; rawText: string },
) {
  return apiFetch<{
    listing_input_id: string;
    llm_fallback?: boolean;
    suggested_values?: Record<string, unknown>;
    evidence_by_field?: Record<string, Array<{ value: unknown; source_snippet?: string }>>;
    field_sources?: Record<string, string>;
    candidates: Record<string, Array<{ value: unknown; final_confidence: string; status: string }>>;
    extraction_warnings: string[];
    agent_claims: Array<{ claim: string; text: string }>;
    sourceType: "url" | "text";
    sourceUrl: string | null;
  }>(`/api/v1/sessions/${sessionId}/smart-paste`, {
    method: "POST",
    body: JSON.stringify(body),
    timeoutMs: LONG_RUNNING_REQUEST_TIMEOUT_MS,
  });
}

export function confirmListing(sessionId: string, body: unknown) {
  return apiFetch(`/api/v1/sessions/${sessionId}/listings/confirm`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function discardListingInput(sessionId: string, listingInputId: string) {
  return apiFetch<void>(`/api/v1/sessions/${sessionId}/listing-inputs/${listingInputId}`, {
    method: "DELETE",
  });
}

export interface GeocodeSuggestion {
  place_id: string;
  description: string;
  main_text: string;
  formatted_address: string;
  latitude: number;
  longitude: number;
}

export async function geocodeAddress(query: string, signal?: AbortSignal) {
  let response: Response;
  try {
    response = await fetch("/api/geocode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
      signal,
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error("Could not reach NearHome address search. Check your connection and retry.");
  }

  const rawBody = await response.text();
  if (!response.ok) throw new Error(parseApiError(rawBody));

  try {
    return JSON.parse(rawBody) as { suggestions: GeocodeSuggestion[] };
  } catch {
    throw new Error("NearHome received an invalid address-search response. Please retry.");
  }
}

export function getRecommendationTrace(sessionId: string) {
  return apiFetch<{ trace_json: Record<string, unknown> }>(`/api/v1/sessions/${sessionId}/recommendation-trace`);
}

export function createObservation(listingId: string, category: string, valueText: string) {
  return apiFetch(`/api/v1/listings/${listingId}/observations`, {
    method: "POST",
    body: JSON.stringify({ category, value_text: valueText }),
  });
}

export function deleteObservation(observationId: string) {
  return apiFetch(`/api/v1/observations/${observationId}`, { method: "DELETE" });
}
