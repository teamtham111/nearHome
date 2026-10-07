type DemoListing = {
  listing_id: string;
  display_name: string;
  address: string;
  asking_price: number;
  floor_area_sqm: number;
  flat_type: string;
  flat_model?: string;
  town?: string;
  remaining_lease_years: number;
  storey_range: string;
};

type DemoState = {
  profile_saved: boolean;
  buyer_profile: Record<string, unknown> | null;
  listings: DemoListing[];
  pending_listing: Record<string, unknown> | null;
  job_started: boolean;
  job_started_at?: number;
};

const key = "nearhome_interactive_demo_state";
const emptyState = (): DemoState => ({ profile_saved: false, buyer_profile: null, listings: [], pending_listing: null, job_started: false });

function getState(): DemoState {
  try {
    const value = localStorage.getItem(key);
    return value ? { ...emptyState(), ...JSON.parse(value) as DemoState } : emptyState();
  } catch {
    return emptyState();
  }
}

function saveState(state: DemoState) {
  localStorage.setItem(key, JSON.stringify(state));
}

const listings = [
  { display_name: "214 Bishan Street 23 · 4-room", address: "Blk 214 Bishan Street 23, Singapore", asking_price: 838000, floor_area_sqm: 112, flat_type: "4 ROOM", town: "BISHAN", remaining_lease_years: 63, storey_range: "", image: "Bishan" },
  { display_name: "11 Holland Drive · 3-room", address: "Blk 11 Holland Drive, Singapore", asking_price: 598888, floor_area_sqm: 65, flat_type: "3 ROOM", flat_model: "Improved", town: "QUEENSTOWN", remaining_lease_years: 47, storey_range: "", image: "Queenstown" },
];

// Real historical records from the repository's HDB transaction fixture, selected
// for nearby blocks with similar flat type, size and lease commencement.
const demoTransactionComparables = [
  [
    { transaction_id: "ingest-223447", transaction_date: "2026-06", block: "229", street: "BISHAN ST 23", town: "BISHAN", flat_type: "4 ROOM", flat_model: "Model A", floor_area_sqm: 102, storey_range: "04 TO 06", remaining_lease: 65, resale_price: 790000, price_per_sqm: 7745.1 },
    { transaction_id: "ingest-223446", transaction_date: "2026-05", block: "220", street: "BISHAN ST 23", town: "BISHAN", flat_type: "4 ROOM", flat_model: "Model A", floor_area_sqm: 103, storey_range: "04 TO 06", remaining_lease: 65.25, resale_price: 775000, price_per_sqm: 7524.27 },
    { transaction_id: "ingest-223445", transaction_date: "2026-05", block: "220", street: "BISHAN ST 23", town: "BISHAN", flat_type: "4 ROOM", flat_model: "Model A", floor_area_sqm: 105, storey_range: "07 TO 09", remaining_lease: 65.25, resale_price: 875000, price_per_sqm: 8333.33 },
    { transaction_id: "ingest-223444", transaction_date: "2026-05", block: "224", street: "BISHAN ST 23", town: "BISHAN", flat_type: "4 ROOM", flat_model: "Model A", floor_area_sqm: 113, storey_range: "01 TO 03", remaining_lease: 65.33, resale_price: 808000, price_per_sqm: 7150.44 },
    { transaction_id: "ingest-223443", transaction_date: "2026-04", block: "216", street: "BISHAN ST 23", town: "BISHAN", flat_type: "4 ROOM", flat_model: "Model A", floor_area_sqm: 114, storey_range: "07 TO 09", remaining_lease: 64.92, resale_price: 915000, price_per_sqm: 8026.32 },
  ],
  [
    { transaction_id: "ingest-230914", transaction_date: "2026-07", block: "13", street: "HOLLAND DR", town: "QUEENSTOWN", flat_type: "3 ROOM", flat_model: "Improved", floor_area_sqm: 65, storey_range: "04 TO 06", remaining_lease: 47.58, resale_price: 450000, price_per_sqm: 6923.08 },
    { transaction_id: "ingest-230913", transaction_date: "2026-06", block: "47", street: "HOLLAND DR", town: "QUEENSTOWN", flat_type: "3 ROOM", flat_model: "Improved", floor_area_sqm: 59, storey_range: "01 TO 03", remaining_lease: 48.08, resale_price: 420000, price_per_sqm: 7118.64 },
    { transaction_id: "ingest-230911", transaction_date: "2026-05", block: "11", street: "HOLLAND DR", town: "QUEENSTOWN", flat_type: "3 ROOM", flat_model: "Improved", floor_area_sqm: 65, storey_range: "01 TO 03", remaining_lease: 47.67, resale_price: 550000, price_per_sqm: 8461.54 },
    { transaction_id: "ingest-230910", transaction_date: "2026-05", block: "11", street: "HOLLAND DR", town: "QUEENSTOWN", flat_type: "3 ROOM", flat_model: "Improved", floor_area_sqm: 65, storey_range: "01 TO 03", remaining_lease: 47.67, resale_price: 480000, price_per_sqm: 7384.62 },
    { transaction_id: "ingest-230909", transaction_date: "2026-05", block: "41", street: "HOLLAND DR", town: "QUEENSTOWN", flat_type: "3 ROOM", flat_model: "Improved", floor_area_sqm: 59, storey_range: "04 TO 06", remaining_lease: 48.17, resale_price: 475888, price_per_sqm: 8065.9 },
  ],
];

function demoFairPrice(listing: DemoListing, index: number) {
  const comparables = demoTransactionComparables[index].map((comparable) => ({
    ...comparable,
    address: `${comparable.block} ${comparable.street}`,
    remaining_lease_months: Math.round(comparable.remaining_lease * 12),
    age_months: Math.max(0, (2026 - Number(comparable.transaction_date.slice(0, 4))) * 12 + 7 - Number(comparable.transaction_date.slice(5, 7))),
  }));
  const prices = comparables.map((row) => row.resale_price);
  const estimate = Math.round(prices.reduce((total, price) => total + price, 0) / prices.length);
  return {
    status: "CALCULATED",
    central_estimate: estimate,
    final_estimate: estimate,
    range_low: Math.min(...prices),
    range_high: Math.max(...prices),
    confidence: "LOW",
    confidence_reasons: ["This estimate is the simple average of five recent transactions on nearby blocks. It does not adjust for floor, renovation or other property-specific differences."],
    comparables,
    displayed_comparables: comparables,
    eligible_transaction_count: comparables.length,
    warnings: ["This is a simple comparison with historical HDB resale prices, not a valuation."],
    remaining_lease_years_used: listing.remaining_lease_years,
    remaining_lease_source: "listing_unverified",
    remaining_lease_confidence: "LOW",
    remaining_lease_estimate: { display_value: `About ${listing.remaining_lease_years} years`, lease_commencement_year: 2026 - (99 - listing.remaining_lease_years), source: "listing_unverified", confidence: "LOW", is_estimated: true, as_of_date: "2026-10" },
    town: listing.town,
    flat_model_used: listing.flat_model ?? "Model A",
    method: "FIVE_TRANSACTION_AVERAGE",
  };
}

const illustrativeFactors = [
  { publicTransport: 82, driving: 74, schools: 76 },
  { publicTransport: 74, driving: 84, schools: 88 },
];

function scoreComponent(name: string, score: number, value: Record<string, unknown>, explanation: string, evidence: Array<Record<string, unknown>> = []) {
  return {
    name, value, score, weight: 0.25, status: "calculated" as const, explanation,
    strengths: [explanation], limitations: ["Illustrative walkthrough result; not a live location assessment."],
    evidence, source: "Illustrative walkthrough data", provenance: "illustrative demo fixture", confidence: "illustrative",
  };
}

function buildRollup(components: ReturnType<typeof scoreComponent>[]) {
  const score = Math.round(components.reduce((total, item) => total + item.score, 0) / components.length);
  return {
    overall_score: score, display_score: score, unrounded_score: score, is_complete: true,
    counts_toward_recommendation: true, coverage_ratio: 1,
    assessed_components: components.map((item) => item.name), excluded_components: [],
    warnings: ["Scores are illustrative walkthrough data."], components,
  };
}

function illustrativeTransport(index: number) {
  const bishops = index === 0;
  return buildRollup([
    scoreComponent("access", bishops ? 79 : 86, { selected_access_path: { access_mode: "direct_walk", station_name: bishops ? "Bishan MRT" : "Queenstown MRT", walk_to_boarding_stop_minutes: bishops ? 8 : 5, station_entry_minutes: 3, total_expected_minutes: bishops ? 11 : 8 } }, "The sample route has a short walk to the rail network."),
    scoreComponent("bus_coverage", bishops ? 86 : 68, { direct_corridors: bishops ? 8 : 5, one_transfer_new_corridors: bishops ? 14 : 10 }, "The sample bus network offers several direct services and transfer options."),
    scoreComponent("mrt_reach", bishops ? 84 : 80, {}, "The sample rail network reaches key destinations with limited transfers.", [{ primary_station_name: bishops ? "Bishan MRT" : "Queenstown MRT", primary_station_lines: bishops ? ["NSL", "CCL"] : ["EWL"], zero_transfer_30: bishops ? 8 : 7, one_transfer_30_incremental: bishops ? 13 : 12, multi_transfer_30_incremental: 4, reachable_station_summaries: {} }]),
    scoreComponent("route_resilience", bishops ? 79 : 63, { fallback_examples: [{ label: "Alternative route option" }, { label: "Second route option" }] }, "The sample route set includes alternatives if a usual route is disrupted."),
  ]);
}

function illustrativeDriving(index: number) {
  const bishops = index === 0;
  return buildRollup([
    scoreComponent("major_road_access", bishops ? 72 : 86, {}, "The sample drive reaches a major road in a short time.", [{ selected: true, expressway: bishops ? "PIE" : "AYE", name: "Sample road entry", peak_duration_minutes: bishops ? 8 : 6, off_peak_duration_minutes: bishops ? 5 : 4 }]),
    scoreComponent("route_connectivity", bishops ? 80 : 84, { distinct_expressways_reached: bishops ? 3 : 4, independent_alternatives: bishops ? 2 : 3, partially_independent_alternatives: 1 }, "Several alternative road corridors are available in this sample assessment."),
    scoreComponent("peak_access_penalty", bishops ? 66 : 78, { penalty_minutes: bishops ? 3 : 2 }, "The sample has a modest peak-hour increase in road access time.", [{ selected: true, peak_duration_minutes: bishops ? 8 : 6, off_peak_duration_minutes: bishops ? 5 : 4, penalty_minutes: bishops ? 3 : 2 }]),
    scoreComponent("parking_convenience", bishops ? 78 : 88, { primary_carpark: { address: bishops ? "Sample nearby carpark · Bishan" : "Sample nearby carpark · Queenstown", walk_minutes: bishops ? 4 : 3, gantry_height_m: 2.1, short_term_parking: "UNKNOWN", availability: {} }, reasonable_carparks_within_500m: bishops ? 2 : 3, reasonable_carparks_within_250m: 1 }, "A sample nearby carpark is within a short walk."),
  ]);
}

function illustrativeSchools(index: number, namedSchools: string[] = []) {
  const closer = index === 1;
  const nearestDistance = closer ? 0.42 : 0.68;
  return {
    score: closer ? 88 : 74, score_status: "calculated" as const, status: "available",
    schools_within_1km: closer ? 3 : 1, schools_within_2km: closer ? 7 : 5,
    nearest_school_distance_km: nearestDistance,
    nearby_schools: [
      { school_name: "Example Primary School", level: "PRIMARY", distance_km: nearestDistance, address: "Illustrative location" },
      { school_name: "Example Secondary School", level: "SECONDARY", distance_km: closer ? 1.1 : 1.4, address: "Illustrative location" },
    ],
    // The first flat sits inside 2 km of a chosen school; the second does not, so the walkthrough shows both outcomes.
    named_school_distances_km: Object.fromEntries(namedSchools.map((name) => [name, closer ? 2.6 : 1.3])),
    matched_named_schools: Object.fromEntries(namedSchools.map((name) => [name, name])),
    explanation: "Illustrative school counts and distances used to demonstrate how the results compare.",
    warnings: ["School locations and distances are illustrative, not live data."],
  };
}

const mockComparison = (state: DemoState) => {
  const ids = state.listings.map((listing) => listing.listing_id);
  const budget = Number(state.buyer_profile?.max_budget ?? 750000);
  const namedSchools = Array.isArray(state.buyer_profile?.named_schools) ? state.buyer_profile.named_schools.map(String) : [];
  const profilePriorities = Array.isArray(state.buyer_profile?.priorities) ? state.buyer_profile.priorities : [];
  const priorityNames = profilePriorities.map((priority) => typeof priority === "string" ? priority : String((priority as Record<string, unknown>).priority_type ?? ""));
  const priorities = priorityNames.length ? priorityNames.slice(0, 3) : ["AFFORDABILITY", "SPACE", "PUBLIC_TRANSPORT"];
  const perListingFit = state.listings.map((listing, index) => {
    const factor = illustrativeFactors[index];
    const scores: Record<string, number> = {
      AFFORDABILITY: Math.max(20, Math.min(100, Math.round((budget / listing.asking_price) * 100))),
      SPACE: Math.max(20, Math.min(100, Math.round((listing.floor_area_sqm / Math.max(...state.listings.map((item) => item.floor_area_sqm))) * 100))),
      PUBLIC_TRANSPORT: factor.publicTransport,
      DRIVING_ACCESS: factor.driving,
      SCHOOLS: factor.schools,
      LEASE: Math.min(100, Math.round((listing.remaining_lease_years / 75) * 100)),
    };
    const weights = priorities.map((_, priorityIndex) => [0.5, 0.3, 0.2][priorityIndex] ?? 0);
    const totalWeight = weights.reduce((sum, weight) => sum + weight, 0) || 1;
    return Math.round(priorities.reduce((sum, priority, priorityIndex) => sum + (scores[priority] ?? 70) * (weights[priorityIndex] ?? 0), 0) / totalWeight);
  });
  const recommendedIndex = perListingFit.length ? perListingFit.indexOf(Math.max(...perListingFit)) : -1;
  const immediate_metrics = state.listings.flatMap((listing, index) => [
    { listing_id: listing.listing_id, metric_name: "asking_price", raw_value: listing.asking_price, unit: "SGD", status: "AVAILABLE", explanation: "Asking price provided in the property listing.", provenance: "property listing" },
    { listing_id: listing.listing_id, metric_name: "price_per_sqm", raw_value: Math.round((listing.asking_price / listing.floor_area_sqm) * 100) / 100, unit: "SGD/sqm", status: "CALCULATED", explanation: "Asking price divided by listed floor area.", formula: "asking price / floor area", provenance: "calculated from property listing" },
    { listing_id: listing.listing_id, metric_name: "budget_difference", raw_value: budget - listing.asking_price, unit: "SGD", status: "CALCULATED", explanation: "Difference from the budget in your buyer profile.", formula: "budget - asking price", provenance: "calculated from buyer profile and property listing" },
    { listing_id: listing.listing_id, metric_name: "floor_area_sqm", raw_value: listing.floor_area_sqm, unit: "sqm", status: "AVAILABLE", explanation: "Floor area provided in the property listing.", provenance: "property listing" },
    { listing_id: listing.listing_id, metric_name: "remaining_lease_years", raw_value: listing.remaining_lease_years, unit: "years", status: "AVAILABLE", explanation: "Remaining lease provided in the property listing.", provenance: "property listing" },
    { listing_id: listing.listing_id, metric_name: "preference_fit_score", raw_value: perListingFit[index], unit: "out of 100", status: "ILLUSTRATIVE", explanation: "Weighted demonstration score based on the three selected priorities.", provenance: "illustrative walkthrough factors" },
  ]);
  return {
    session_id: "demo-session", listing_count: ids.length, can_compare: ids.length >= 2,
    immediate_metrics,
    preference_scores: ids.map((id, index) => ({ listing_id: id, overall_fit_score: perListingFit[index], total_score: perListingFit[index], rank: 1 + perListingFit.filter((score) => score > perListingFit[index]).length, coverage: 1, sub_scores: {} })),
    recommendation: { recommended_listing_id: ids[recommendedIndex] ?? null, is_tie: perListingFit.filter((score) => score === perListingFit[recommendedIndex]).length > 1, is_provisional: false, one_sentence_summary: `${state.listings[recommendedIndex]?.display_name ?? "The leading listing"} scores highest against the three priorities selected for this illustrative walkthrough. The factor scores show where each flat has an advantage.`, confidence: "ILLUSTRATIVE", confidence_reasons: ["Connectivity, school and preference-fit scores are illustrative walkthrough data."], why_not_selected: {}, decision_hinge: "Compare the selected factors side by side; changing priority order changes the weighted fit score." },
    fair_price_status: "CALCULATED", fair_price_by_listing: Object.fromEntries(ids.map((id, index) => [id, demoFairPrice(state.listings[index], index)])),
    transport_by_listing: Object.fromEntries(ids.map((id, index) => [id, illustrativeTransport(index)])),
    driving_by_listing: Object.fromEntries(ids.map((id, index) => [id, illustrativeDriving(index)])),
    schools_by_listing: Object.fromEntries(ids.map((id, index) => [id, illustrativeSchools(index, namedSchools)])),
    journey_results: ids.map((id, index) => ({ listing_id: id, important_location_id: "demo-location-0", mode: "PUBLIC_TRANSPORT", duration_minutes: index === 0 ? 31 : 25, difference_from_fastest_seconds: index === 0 ? 360 : 0, is_fastest: index === 1, status: "AVAILABLE", provider: "ILLUSTRATIVE", requested_day_type: "WEEKDAY", requested_time_local: "08:00:00" })),
    regular_destination_journeys: ids.map((id, index) => ({ journey_estimate_id: `demo-drive-${index + 1}`, listing_id: id, important_location_id: "demo-location-0", destination_label: "Raffles Place", destination_address: "Raffles Place, Singapore", selected_day_type: "WEEKDAY", selected_time_local: "08:00:00", duration_minutes: index === 0 ? 24 : 19, difference_from_fastest_seconds: index === 0 ? 300 : 0, is_fastest: index === 1, status: "AVAILABLE", provider: "ILLUSTRATIVE", source: "ILLUSTRATIVE", confidence: "LOW" })),
    observations: [],
    enrichment_summary: ids.flatMap((listing_id) => ["PROPERTY_DATA", "LEASE", "TRANSACTION_DATA", "SCHOOLS", "PUBLIC_TRANSPORT", "DRIVING_ACCESS"].map((enrichment_type) => ({ listing_id, enrichment_type, status: "SUCCEEDED" }))), demo_mode: true,
  };
};

export function mockDemoApi<T>(path: string, init?: RequestInit): T | undefined {
  if (typeof window === "undefined") return undefined;
  // The query entry point can render child queries before DemoShell's effect runs.
  // Arm interception synchronously so a demo visit can never reach the real API.
  const inDemo = new URLSearchParams(window.location.search).get("demo") === "1"
    || document.body.dataset.demoActive === "true";
  if (!inDemo) return undefined;
  localStorage.setItem("nearhome_interactive_demo", "1");
  const method = (init?.method ?? "GET").toUpperCase();
  const state = getState();
  const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
  let value: unknown;
  if (path.endsWith("/api/v1/sessions") && method === "POST") {
    value = { session_id: "demo-session", demo_mode: true, created_at: new Date().toISOString() };
  } else if (path.includes("/api/v1/sessions/demo-session/buyer-profile") && method === "PUT") {
    state.profile_saved = true;
    state.buyer_profile = body;
    saveState(state);
    value = { saved: true };
  } else if (path.includes("/api/v1/sessions/demo-session/smart-paste")) {
    const listing = listings[state.listings.length] ?? listings[1];
    // The first pasted listing leaves out its lease so the walkthrough can show a flagged field.
    const flagLease = state.listings.length === 0;
    const fields = { display_name: listing.display_name, address: listing.address, asking_price: listing.asking_price, floor_area_sqm: listing.floor_area_sqm, flat_type: listing.flat_type, flat_type_raw: listing.flat_type, flat_model: listing.flat_model, ...(flagLease ? {} : { remaining_lease_years: listing.remaining_lease_years }) };
    state.pending_listing = fields;
    saveState(state);
    value = { listing_input_id: `demo-input-${state.listings.length + 1}`, suggested_values: fields, candidates: {}, evidence_by_field: {}, field_sources: {}, extraction_warnings: flagLease ? ["We couldn't find the remaining lease in this listing. Please check it before confirming."] : [], agent_claims: [], sourceType: "text", sourceUrl: null };
  } else if (path.includes("/api/v1/sessions/demo-session/listings/confirm") && method === "POST") {
    const fields = state.pending_listing ?? body;
    const listing = listings[state.listings.length] ?? listings[1];
    state.listings.push({ ...listing, ...fields, ...(Number(body.remaining_lease_years) > 0 ? { remaining_lease_years: Number(body.remaining_lease_years) } : {}), listing_id: `demo-flat-${state.listings.length + 1}` } as DemoListing);
    state.pending_listing = null;
    saveState(state);
    value = state.listings[state.listings.length - 1];
  } else if (path.includes("/api/v1/sessions/demo-session") && path.endsWith("/comparison")) {
    value = mockComparison(state);
  } else if (path.includes("/api/v1/sessions/demo-session/enrichment/start")) {
    state.job_started = true;
    state.job_started_at = Date.now();
    saveState(state);
    value = { job_id: "demo-job", status: "running", status_url: "/api/v1/jobs/demo-job?session_id=demo-session" };
  } else if (path.includes("/api/v1/jobs/demo-job")) {
    const elapsed = Date.now() - (state.job_started_at ?? Date.now());
    const completed = elapsed > 1_400;
    const progressStage = completed ? "completed" : elapsed < 250 ? "geocoding_and_property" : elapsed < 500 ? "checking_schools" : elapsed < 750 ? "calculating_fair_price" : elapsed < 1_000 ? "calculating_transport" : "calculating_driving";
    value = { job_id: "demo-job", session_id: "demo-session", status: completed ? "completed" : "running", progress_stage: progressStage, attempts: 1, error_code: null, error_message: null, result_available: completed, created_at: new Date().toISOString(), started_at: new Date().toISOString(), completed_at: completed ? new Date().toISOString() : null, updated_at: new Date().toISOString() };
  } else if (path.includes("/enrichment/status")) {
    const elapsed = Date.now() - (state.job_started_at ?? Date.now());
    const statusFor: Record<string, string> = {
      GEOCODING: elapsed > 180 ? "SUCCEEDED" : "RUNNING",
      PROPERTY_DATA: elapsed > 180 ? "SUCCEEDED" : "RUNNING",
      LEASE: elapsed > 180 ? "SUCCEEDED" : "RUNNING",
      TRANSACTION_DATA: elapsed > 1_400 ? "SUCCEEDED" : elapsed > 180 ? "RUNNING" : "QUEUED",
      SCHOOLS: elapsed > 500 ? "SUCCEEDED" : "RUNNING",
      FAIR_PRICE: elapsed > 750 ? "SUCCEEDED" : "QUEUED",
      PUBLIC_TRANSPORT: elapsed > 1_000 ? "SUCCEEDED" : "QUEUED",
      DRIVING_ACCESS: elapsed > 1_250 ? "SUCCEEDED" : "RUNNING",
    };
    value = { runs: state.listings.flatMap((listing) => Object.entries(statusFor).map(([enrichment_type, status]) => ({ listing_id: listing.listing_id, enrichment_type, status }))) };
  } else if (path.includes("/api/v1/sessions/demo-session")) {
    const profile = state.buyer_profile ?? {};
    const savedLocations = Array.isArray(profile.important_locations) ? profile.important_locations as Array<Record<string, unknown>> : [];
    const priorities = Array.isArray(profile.priorities) ? (profile.priorities as Array<Record<string, unknown>>).map((priority) => String(priority.priority_type ?? "")) : [];
    value = { session_id: "demo-session", profile_saved: state.profile_saved, buyer_profile: state.buyer_profile ? { max_budget: Number(profile.max_budget ?? 750000), main_transport_mode: String(profile.main_transport_mode ?? "MAINLY_PUBLIC_TRANSPORT"), schools_matter: Boolean(profile.schools_matter), named_schools: profile.named_schools ?? [], named_school: null, priorities, important_locations: savedLocations.map((location, index) => ({ important_location_id: `demo-location-${index}`, ...location, is_complete: true })) } : null, listings: state.listings, listing_count: state.listings.length, demo_mode: true };
  } else if (path.includes("/api/geocode")) {
    const q = new URLSearchParams(path.split("?")[1] ?? "").get("q") ?? "";
    value = { suggestions: [{ place_id: "demo-place", description: q.includes("school") ? "Ang Mo Kio, Singapore" : "Raffles Place, Singapore", main_text: q.includes("school") ? "Ang Mo Kio Primary School" : "Raffles Place", formatted_address: "Raffles Place, Singapore", latitude: 1.284, longitude: 103.852 }] };
  } else if (method === "DELETE") {
    value = {};
  }
  return value as T | undefined;
}

export function resetDemoFixtures() {
  localStorage.removeItem(key);
}

export function startDemoEnrichmentFixture() {
  const state = getState();
  state.job_started = true;
  state.job_started_at = Date.now();
  saveState(state);
}

export function snapshotDemoFixtures() {
  return localStorage.getItem(key);
}

export function restoreDemoFixtures(snapshot: string | null) {
  if (snapshot === null) localStorage.removeItem(key);
  else localStorage.setItem(key, snapshot);
}

export function getDemoGeocode(query: string) {
  const school = /school|primary|secondary|institution/i.test(query);
  const name = school ? "Ang Mo Kio Primary School" : "Raffles Place";
  return { suggestions: [{ place_id: "demo-place", description: `${name}, Singapore`, main_text: name, formatted_address: `${name}, Singapore`, latitude: school ? 1.369 : 1.284, longitude: school ? 103.849 : 103.852 }] };
}
