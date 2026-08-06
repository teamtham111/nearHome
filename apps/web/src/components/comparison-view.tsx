"use client";

import { useId, useState, type ReactNode } from "react";

import type {
  ComparisonResponse,
  ComponentResultData,
  FairPriceComparable,
  FairPriceData,
  ModelRollupData,
  SessionListing,
} from "@/lib/api";
import {
  buildComparableReason,
  buildConfidenceExplanation,
  buildFilterSummary,
  buildPriceExplanation,
  buildValuationLimitations,
  comparableLeaseDisplay,
  comparableDate,
  displayedComparables,
  eligibleComparableCount,
  fairPriceModelDisplayName,
  formatValuationDifference,
  getComparableMatchLabel,
  getHumanReadableEvidenceSource,
  leaseDisplay,
  getPriceAssessment,
  resolveAskingPrice,
} from "@/lib/fair-price";
import {
  buildTransportExplanation,
  buildTransportHeadline,
  comparisonPosition,
  componentByName,
  displayScore,
  formatDisplayScore,
  lineName,
  ratingForScore,
  transferCoverageLabel,
} from "@/lib/public-transport";
import {
  availabilitySnapshot,
  drivingComponent,
  drivingExplanation,
  drivingHeadline,
  drivingStatus,
  displayDrivingScore,
  formatDrivingScore,
  parkingFacts,
  parkingPrimary,
  peakResult,
  roadResult,
  routeResult,
  unavailableDrivingComponentNames,
} from "@/lib/driving";
import { ScoreRing } from "@/components/score-ring";

interface Props {
  data: ComparisonResponse;
  session?: {
    listings: SessionListing[];
    buyer_profile?: {
      main_transport_mode: string;
      schools_matter: boolean;
      named_schools?: string[];
      named_school?: string | null;
      important_locations?: Array<{ important_location_id: string; label: string; formatted_address?: string | null; transport_mode?: string | null; is_complete?: boolean }>;
    } | null;
  };
}

function formatCurrency(value: unknown) {
  if (value === null || value === undefined) return "—";
  return `S$${Number(value).toLocaleString("en-SG")}`;
}

function formatFiniteCurrency(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? `S$${Math.round(value).toLocaleString("en-SG")}`
    : null;
}

function formatBudgetDifference(value: unknown) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const amount = Number(value);
  const absolute = `S$${Math.abs(amount).toLocaleString("en-SG")}`;
  if (amount > 0) return `+${absolute} (${absolute} under budget)`;
  if (amount < 0) return `-${absolute} (${absolute} over budget)`;
  return "S$0 (at budget)";
}

type SchoolAssessment = ComparisonResponse["schools_by_listing"][string];

function formatSchoolDistance(distanceKm: number | null | undefined) {
  if (typeof distanceKm !== "number" || !Number.isFinite(distanceKm)) return "—";
  if (distanceKm < 1) return `${Math.round(distanceKm * 1000)} m`;
  return `${Math.round(distanceKm * 100) / 100} km`;
}

function isSchoolAssessmentAvailable(assessment: SchoolAssessment | undefined) {
  if (!assessment) return false;
  return !["missing_input", "unavailable", "error"].includes(assessment.score_status ?? "");
}

function schoolAccessRating(assessment: SchoolAssessment) {
  const withinOneKm = assessment.schools_within_1km ?? 0;
  const withinTwoKm = assessment.schools_within_2km ?? 0;
  if (withinOneKm >= 2 || (assessment.score ?? 0) >= 30) {
    return { label: "Strong school access", className: "bg-green-100 text-green-800", tone: "strong" as const };
  }
  if (withinOneKm >= 1 || withinTwoKm >= 2) {
    return { label: "Moderate school access", className: "bg-amber-100 text-amber-800", tone: "moderate" as const };
  }
  if (withinTwoKm > 0) {
    return { label: "Limited nearby access", className: "bg-slate-100 text-slate-700", tone: "limited" as const };
  }
  return { label: "Limited nearby access", className: "bg-slate-100 text-slate-700", tone: "none" as const };
}

function schoolsComparisonWinner(
  listingIds: string[],
  byListing: Record<string, SchoolAssessment>,
) {
  const assessed = listingIds
    .map((listingId) => ({ listingId, assessment: byListing[listingId] }))
    .filter((entry): entry is { listingId: string; assessment: SchoolAssessment } => isSchoolAssessmentAvailable(entry.assessment));
  if (assessed.length < 2) return null;
  const ranked = [...assessed].sort((a, b) => {
    const oneKmDifference = (b.assessment.schools_within_1km ?? 0) - (a.assessment.schools_within_1km ?? 0);
    if (oneKmDifference) return oneKmDifference;
    const twoKmDifference = (b.assessment.schools_within_2km ?? 0) - (a.assessment.schools_within_2km ?? 0);
    if (twoKmDifference) return twoKmDifference;
    return (a.assessment.nearest_school_distance_km ?? Infinity) - (b.assessment.nearest_school_distance_km ?? Infinity);
  });
  const [winner, runnerUp] = ranked;
  const hasMoreWithinOneKm = (winner.assessment.schools_within_1km ?? 0) > (runnerUp.assessment.schools_within_1km ?? 0);
  const hasMoreWithinTwoKm = (winner.assessment.schools_within_2km ?? 0) > (runnerUp.assessment.schools_within_2km ?? 0);
  const nearestDifference = (runnerUp.assessment.nearest_school_distance_km ?? Infinity) - (winner.assessment.nearest_school_distance_km ?? Infinity);
  return hasMoreWithinOneKm || hasMoreWithinTwoKm || nearestDifference >= 0.1 ? winner : null;
}

function NearbySchoolsPanel({
  listingIds,
  listingNames,
  byListing,
  selectedSchools,
}: {
  listingIds: string[];
  listingNames: Record<string, string>;
  byListing: Record<string, SchoolAssessment>;
  selectedSchools: string[];
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const winner = schoolsComparisonWinner(listingIds, byListing);
  const availableAssessments = listingIds.map((id) => byListing[id]).filter(isSchoolAssessmentAvailable);
  const unmatchedSchools = selectedSchools.filter((school) =>
    availableAssessments.length > 0 && availableAssessments.every((assessment) => {
      if (assessment.matched_named_schools && school in assessment.matched_named_schools) {
        return assessment.matched_named_schools[school] === null;
      }
      return assessment.named_school_distances_km?.[school] === null;
    }),
  );

  return (
    <section className="nh-card" aria-labelledby="schools-heading">
      <h3 id="schools-heading" className="text-lg font-semibold">Nearby schools</h3>
      <p className="mt-1 text-xs text-slate-500">Approximate distances based on the current MOE reference snapshot. Distance does not guarantee admission, registration priority or eligibility.</p>
      <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3" aria-live="polite">
        {winner ? (
          <>
            <p className="font-semibold text-slate-900">Better for nearby schools: {listingNames[winner.listingId] ?? winner.listingId.slice(0, 8)}</p>
            <p className="mt-1 text-sm text-slate-700">
              {winner.assessment.schools_within_1km ?? 0} schools are within 1 km{winner.assessment.nearest_school_distance_km != null ? `, with the nearest approximately ${formatSchoolDistance(winner.assessment.nearest_school_distance_km)} away.` : "."}
            </p>
          </>
        ) : availableAssessments.length === 0 ? (
          <>
            <p className="font-semibold text-slate-900">Nearby-school access is not assessed</p>
            <p className="mt-1 text-sm text-slate-700">Run enrichment after listing coordinates and the MOE reference data are available.</p>
          </>
        ) : (
          <>
            <p className="font-semibold text-slate-900">Similar nearby-school access</p>
            <p className="mt-1 text-sm text-slate-700">The available school counts and nearest-school distances do not show a clear advantage.</p>
          </>
        )}
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {listingIds.map((id) => {
          const assessment = byListing[id];
          const isAvailable = isSchoolAssessmentAvailable(assessment);
          const detailsId = `nearby-school-list-${id}`;
          if (!isAvailable) {
            return (
              <article key={id} className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                <h4 className="font-semibold text-slate-900">{listingNames[id] ?? id.slice(0, 8)}</h4>
                <p className="mt-2 font-medium text-slate-700">Not assessed</p>
                <p className="mt-1 text-sm text-slate-600">{assessment?.missing_reasons?.join("; ") ?? "Run enrichment to calculate nearby schools."}</p>
              </article>
            );
          }
          const rating = schoolAccessRating(assessment);
          const nearbySchools = assessment.nearby_schools ?? [];
          const matchedOfficialNames = new Set(Object.values(assessment.matched_named_schools ?? {}).filter((name): name is string => Boolean(name)));
          const isWinner = winner?.listingId === id;
          return (
            <article key={id} className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <h4 className="font-semibold text-slate-900">{listingNames[id] ?? id.slice(0, 8)}</h4>
                {isWinner && <span className="rounded-full bg-green-100 px-2 py-1 text-xs font-semibold text-green-800">Stronger</span>}
              </div>
              <p className={`mt-2 inline-flex rounded-full px-2 py-1 text-sm font-medium ${rating.className}`}>{rating.label}</p>
              <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                <dt className="text-slate-600">Within 1 km</dt><dd className="font-semibold text-slate-900">{assessment.schools_within_1km ?? 0}</dd>
                <dt className="text-slate-600">Within 2 km</dt><dd className="font-semibold text-slate-900">{assessment.schools_within_2km ?? 0} total</dd>
                {assessment.nearest_school_distance_km != null && <><dt className="text-slate-600">Nearest</dt><dd className="font-semibold text-slate-900">{formatSchoolDistance(assessment.nearest_school_distance_km)}</dd></>}
              </dl>
              {nearbySchools.length === 0 ? (
                <p className="mt-4 text-sm text-slate-600">No nearby schools found</p>
              ) : (
                <>
                  <button
                    type="button"
                    className="mt-4 flex min-h-11 w-full items-center justify-between rounded-lg border border-slate-300 px-3 text-left text-sm font-medium text-slate-800 hover:border-teal-500 focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-2"
                    aria-expanded={Boolean(expanded[id])}
                    aria-controls={detailsId}
                    onClick={() => setExpanded((current) => ({ ...current, [id]: !current[id] }))}
                  >
                    <span>{expanded[id] ? "Hide" : "View"} {nearbySchools.length} nearby schools</span>
                    <span aria-hidden="true" className={`transition-transform ${expanded[id] ? "rotate-180" : ""}`}>⌄</span>
                  </button>
                  {expanded[id] && (
                    <div id={detailsId} className="mt-3 border-t border-slate-100 pt-3">
                      <ul className="space-y-2 text-sm">
                        {nearbySchools.map((school) => (
                          <li key={`${school.school_name}-${school.distance_km}`} className="flex items-start justify-between gap-3">
                            <span><span className="font-medium text-slate-900">{school.school_name}</span>{school.level ? <span className="ml-1 text-slate-500">· {humanizeLabel(school.level.toLowerCase())}</span> : null}{matchedOfficialNames.has(school.school_name) ? <span className="ml-2 rounded bg-teal-50 px-1.5 py-0.5 text-xs text-teal-800">Selected</span> : null}</span>
                            <span className="shrink-0 text-slate-700">{formatSchoolDistance(school.distance_km)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </article>
          );
        })}
      </div>
      {unmatchedSchools.length > 0 && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3" role="alert">
          <p className="font-medium text-amber-900">⚠ We could not match {unmatchedSchools.length} selected school{unmatchedSchools.length === 1 ? "" : "s"}</p>
          <p className="mt-1 text-sm text-amber-800">{unmatchedSchools.join(" · ")}</p>
          <a className="mt-2 inline-block text-sm font-medium text-teal-800 underline" href="#buyer-profile">Review selected school names</a>
        </div>
      )}
    </section>
  );
}

function budgetDifferenceClass(value: unknown) {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount === 0) return "text-slate-700";
  return amount > 0 ? "text-green-700" : "text-red-700";
}

function humanizeLabel(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function readableSource(value: string) {
  const names: Record<string, string> = {
    GOOGLE_ROUTES: "Google Routes",
    HDB_TRANSACTIONS: "HDB resale transactions",
    LTA_REFERENCE: "LTA reference data",
    MOCK: "Demo estimate",
  };
  return names[value] ?? humanizeLabel(value);
}

function FairPricePanel({
  listingIds,
  listingNames,
  listings,
  fairPriceByListing,
  status,
}: {
  listingIds: string[];
  listingNames: Record<string, string>;
  listings: SessionListing[];
  fairPriceByListing: Record<string, FairPriceData>;
  status: string;
}) {
  if (status === "AWAITING_ENRICHMENT" || status === "NOT_STARTED") {
    return (
      <p className="mt-2 text-sm text-slate-600">
        {status === "NOT_STARTED"
          ? "Run enrichment to calculate fair-price estimates."
          : "Awaiting transaction enrichment."}{" "}
        This is an analytical estimate, not an official HDB valuation.
      </p>
    );
  }

  const listingById = Object.fromEntries(listings.map((listing) => [listing.listing_id, listing]));

  return (
    <div className="mt-4 space-y-3">
      {listingIds.map((id) => {
        const fp = fairPriceByListing[id];
        const listing = listingById[id];
        if (!fp) {
          return (
            <div key={id} className="rounded-lg border border-slate-200 bg-white p-3">
              <h4 className="font-medium">{listingNames[id] ?? id.slice(0, 8)}</h4>
              <p className="text-sm text-slate-500">No fair-price data yet</p>
            </div>
          );
        }
        if (fp.status === "INSUFFICIENT_EVIDENCE") {
          return (
            <div key={id} className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <h4 className="font-medium">{listingNames[id] ?? id.slice(0, 8)}</h4>
              <p className="mt-1 text-sm text-amber-800">No reliable estimate is available yet.</p>
              <p className="mt-1 text-xs text-amber-700">
                {buildConfidenceExplanation(fp)}
              </p>
            </div>
          );
        }
        const askingPrice = resolveAskingPrice(listing, fp);
        const difference = formatValuationDifference(askingPrice, fp.central_estimate);
        const assessment = getPriceAssessment(difference.percentage);
        const priceExplanation = buildPriceExplanation(askingPrice, fp, difference);
        const strongestComparables = displayedComparables(fp);
        const limitations = buildValuationLimitations(fp, listing);
        return (
          <details key={id} className="rounded-lg border border-slate-200 bg-white p-3">
            <summary className="cursor-pointer list-none rounded focus:outline-none focus:ring-2 focus:ring-teal-600">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h4 className="font-medium">{listingNames[id] ?? id.slice(0, 8)}</h4>
                  <p className="mt-1 text-sm text-slate-700">
                    <span className="font-semibold">Estimated value: {formatCurrency(fp.central_estimate)}</span>
                    <span className="mx-2 text-slate-400">·</span>
                    {difference.kind === "unavailable" ? "Asking price unavailable" : `Asking price: ${formatCurrency(askingPrice)}`}
                  </p>
                  <p className="mt-1 text-sm font-medium text-slate-800">{difference.label}</p>
                  <p className="mt-1 text-sm text-slate-700">{assessment?.label ?? "Assessment unavailable"}</p>
                </div>
                <span className="text-sm text-teal-700">See valuation evidence</span>
              </div>
              <p className="mt-2 text-xs text-slate-600">
                Likely range: {fp.range_low != null && fp.range_high != null ? `${formatCurrency(fp.range_low)} – ${formatCurrency(fp.range_high)}` : "Unavailable"}
                <span className="mx-2 text-slate-400">·</span>
                Confidence: {fp.confidence ? fp.confidence.toLowerCase() : "unavailable"}
              </p>
            </summary>
            <div className="mt-4 space-y-5 border-t border-slate-100 pt-4">
              <section aria-labelledby={`valuation-summary-${id}`}>
                <h5 id={`valuation-summary-${id}`} className="text-base font-semibold">Estimated market value</h5>
                <p className="mt-1 text-lg font-semibold">{formatCurrency(fp.central_estimate)}</p>
                <p className="mt-1 text-sm text-slate-700">
                  Likely range: {fp.range_low != null && fp.range_high != null ? `${formatCurrency(fp.range_low)} – ${formatCurrency(fp.range_high)}` : "Unavailable"}
                </p>
                <div className="mt-3 overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 text-left">
                        <th className="px-2 py-2 font-medium">Asking price</th>
                        <th className="px-2 py-2 font-medium">Difference from estimate</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td className="px-2 py-2">{difference.kind === "unavailable" ? "Unavailable" : formatCurrency(askingPrice)}</td>
                        <td className="px-2 py-2">{difference.label}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <h6 className="mt-4 font-semibold">{assessment?.label ?? "Pricing assessment unavailable"}</h6>
                {priceExplanation && <p className="mt-1 text-sm text-slate-700">{priceExplanation}</p>}
                {assessment && <p className="mt-1 text-sm text-slate-600">{assessment.explanation}</p>}
                <p className="mt-3 text-sm font-medium">Confidence: {fp.confidence ? fp.confidence.toLowerCase() : "unavailable"}</p>
                <p className="mt-1 text-sm text-slate-600">{buildConfidenceExplanation(fp)}</p>
              </section>

              <section aria-labelledby={`why-estimate-${id}`}>
                <h5 id={`why-estimate-${id}`} className="text-base font-semibold">Why this estimate?</h5>
                <p className="mt-1 text-sm text-slate-700">{buildFilterSummary(fp, listing)}</p>
                <div className="mt-3 overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <tbody>
                      <tr className="border-b border-slate-100"><th className="px-2 py-2 text-left font-medium">Town</th><td className="px-2 py-2">{fp.town ?? "Not confirmed"}</td></tr>
                      <tr className="border-b border-slate-100"><th className="px-2 py-2 text-left font-medium">Flat type</th><td className="px-2 py-2">{listing?.flat_type ?? "Not provided"}</td></tr>
                      <tr className="border-b border-slate-100"><th className="px-2 py-2 text-left font-medium">Flat model</th><td className="px-2 py-2">{fp.flat_model_used ?? listing?.flat_model ?? "Not provided"}</td></tr>
                      <tr className="border-b border-slate-100"><th className="px-2 py-2 text-left font-medium">Estimated remaining lease</th><td className="px-2 py-2">{leaseDisplay(fp, listing)}</td></tr>
                      <tr><th className="px-2 py-2 text-left font-medium">Storey</th><td className="px-2 py-2">{listing?.storey_range ?? "Not provided"}</td></tr>
                    </tbody>
                  </table>
                </div>
              </section>

              <section aria-labelledby={`property-info-${id}`}>
                <h5 id={`property-info-${id}`} className="text-base font-semibold">Property information</h5>
                <div className="mt-2 space-y-1 text-sm text-slate-700">
                  <p><strong>Estimated remaining lease:</strong> {leaseDisplay(fp, listing)}</p>
                  {fp.remaining_lease_estimate?.lease_commencement_year != null && <p><strong>Lease commenced:</strong> {fp.remaining_lease_estimate.lease_commencement_year}</p>}
                  <p><strong>Source:</strong> {getHumanReadableEvidenceSource(fp.remaining_lease_estimate?.source ?? fp.remaining_lease_source)}</p>
                  {(fp.remaining_lease_estimate?.as_of_date ?? fp.remaining_lease_as_of_date) && <p><strong>Data checked:</strong> {fp.remaining_lease_estimate?.as_of_date ?? fp.remaining_lease_as_of_date}</p>}
                </div>
              </section>

              <section aria-labelledby={`comparables-${id}`}>
                <h5 id={`comparables-${id}`} className="text-base font-semibold">Similar recent transactions</h5>
                {strongestComparables.length > 0 ? (
                  <>
                    <p className="mt-1 text-sm text-slate-600">
                      {strongestComparables.length} closest match{strongestComparables.length === 1 ? "" : "es"} shown from {eligibleComparableCount(fp)?.toLocaleString("en-SG") ?? "the eligible"} eligible record{eligibleComparableCount(fp) === 1 ? "" : "s"}. These examples provide market context; the estimate is not calculated by averaging only these {strongestComparables.length} transactions.
                    </p>
                    <ComparableTable comparables={strongestComparables} listing={listing} />
                    <details className="mt-3">
                      <summary className="cursor-pointer text-sm text-teal-700">How these were selected · How the estimate works</summary>
                      <p className="mt-2 text-sm text-slate-600">
                        {fairPriceModelDisplayName(fp) ? `This ${fairPriceModelDisplayName(fp)} estimate is generated from patterns in historical HDB resale transactions.` : "This estimate is generated from patterns in historical HDB resale transactions."} The transactions shown here are the {strongestComparables.length} closest recent matches and are provided to help you judge whether the estimate appears reasonable. They are not the only records considered by the valuation system, and the estimate is not calculated by simply averaging these transactions.
                      </p>
                    </details>
                  </>
                ) : (
                  <p className="mt-1 text-sm text-slate-600">No comparable transactions are available for display.</p>
                )}
              </section>

              {limitations.length > 0 && (
                <section aria-labelledby={`limitations-${id}`}>
                  <h5 id={`limitations-${id}`} className="text-base font-semibold">What could affect the valuation?</h5>
                  <div className="mt-2 space-y-3">
                    {limitations.map((limitation) => (
                      <div key={limitation.title}>
                        <h6 className="font-medium">{limitation.title}</h6>
                        <p className="mt-1 text-sm text-slate-600">{limitation.description}</p>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </div>
          </details>
        );
      })}
      <p className="text-xs text-slate-500">NearHome never presents fair price as an official HDB valuation.</p>
    </div>
  );
}

function ComparableTable({ comparables, listing }: { comparables: FairPriceComparable[]; listing: SessionListing | undefined }) {
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left">
            <th className="px-2 py-2 font-medium">Transaction</th>
            <th className="px-2 py-2 font-medium">Sale price</th>
            <th className="px-2 py-2 font-medium">Floor area</th>
            <th className="px-2 py-2 font-medium">Remaining lease</th>
            <th className="px-2 py-2 font-medium">Storey</th>
            <th className="px-2 py-2 font-medium">Similarity</th>
          </tr>
        </thead>
        <tbody>
          {comparables.map((comparable, index) => (
            <tr key={String(comparable.transaction_id ?? `${comparable.transaction_date}-${comparable.block}-${index}`)} className="border-b border-slate-100 align-top">
              <td className="px-2 py-2">
                {comparable.address ?? `${comparable.block ?? "—"} ${comparable.street ?? ""}`.trim()} · {comparableDate(comparable)}
                <details className="mt-1">
                  <summary className="cursor-pointer text-xs text-teal-700">Why this is comparable</summary>
                  <p className="mt-1 text-xs text-slate-600">{buildComparableReason(comparable, listing)}</p>
                </details>
              </td>
              <td className="px-2 py-2">{comparable.resale_price != null ? formatCurrency(comparable.resale_price) : "Unavailable"}</td>
              <td className="px-2 py-2">{comparable.floor_area_sqm != null ? `${comparable.floor_area_sqm} sqm` : "Not available"}</td>
              <td className="px-2 py-2">{comparableLeaseDisplay(comparable)}</td>
              <td className="px-2 py-2">{comparable.storey_range ?? "Not available"}</td>
              <td className="px-2 py-2">{getComparableMatchLabel(comparable.similarity)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function componentFor(rollup: ModelRollupData, name: string) {
  return rollup.components.find((component) => component.name === name);
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function naturalAccessLabel(path: Record<string, unknown>): string {
  const mode = path.access_mode ?? path.path_type;
  if (mode === "walk_to_bus" || mode === "walk_to_bus_stop") return `Walk to ${path.name ?? "a nearby bus stop"}`;
  if (mode === "feeder_bus") return `Take a feeder bus to ${path.station_name ?? "an MRT station"}`;
  if (mode === "direct_walk") return `Walk to ${path.station_name ?? "an MRT station"}`;
  return "Use the best confirmed network-entry option";
}

function minutesText(minutes: number | null, status: "available" | "not_available" | "not_applicable") {
  if (status === "not_applicable") return "Not applicable";
  if (status === "not_available" || minutes == null) return "Not available";
  return `${Math.round(minutes)} min`;
}

function accessStages(path: Record<string, unknown> | null) {
  if (!path) return [];
  const mode = path.access_mode ?? path.path_type;
  const walk = numberValue(path.walk_to_boarding_stop_minutes ?? path.walk_minutes);
  const wait = numberValue(path.scheduled_wait_proxy_minutes);
  const inVehicle = numberValue(path.in_vehicle_minutes);
  const stationEntry = numberValue(path.station_entry_minutes);
  const total = numberValue(path.total_expected_minutes);
  if (mode === "walk_to_bus" || mode === "walk_to_bus_stop") {
    return [
      ["Walk to bus stop", walk, walk == null ? "not_available" : "available"],
      ["Typical waiting time", wait, wait == null ? "not_available" : "available"],
      ["Feeder-bus journey", null, "not_applicable"],
      ["Station-entry time", null, "not_applicable"],
      ["Total network-entry time", total, total == null ? "not_available" : "available"],
    ] as Array<[string, number | null, "available" | "not_available" | "not_applicable"]>;
  }
  if (mode === "feeder_bus") {
    return [
      ["Walk to bus stop", walk, walk == null ? "not_available" : "available"],
      ["Typical waiting time", wait, wait == null ? "not_available" : "available"],
      ["Feeder-bus journey", inVehicle, inVehicle == null ? "not_available" : "available"],
      ["Station-entry time", stationEntry, stationEntry == null ? "not_available" : "available"],
      ["Total network-entry time", total, total == null ? "not_available" : "available"],
    ] as Array<[string, number | null, "available" | "not_available" | "not_applicable"]>;
  }
  return [
    ["Walk to MRT", walk, walk == null ? "not_available" : "available"],
    ["Typical waiting time", null, "not_applicable"],
    ["Station-entry time", stationEntry, stationEntry == null ? "not_available" : "available"],
    ["Total network-entry time", total, total == null ? "not_available" : "available"],
  ] as Array<[string, number | null, "available" | "not_available" | "not_applicable"]>;
}

function accessExplanation(component: ComponentResultData, path: Record<string, unknown> | null): string {
  const score = displayScore(component.score);
  const total = numberValue(path?.total_expected_minutes);
  if (total == null) return `The best confirmed network-entry option is ${score.rating?.toLowerCase() ?? "not available"}; the journey details are incomplete.`;
  if (score.rating === "Excellent" || score.rating === "Good") return `The best available option enters the network in about ${Math.round(total)} minutes.`;
  return `The best available option requires about ${Math.round(total)} minutes before entering the public-transport network. A shorter walk or convenient feeder connection would score higher.`;
}

type CrossListingFactor = [string, (rollup: ModelRollupData | undefined) => number | null | undefined];

function ShortlistedHomesComparison({
  headingId,
  listingIds,
  listingNames,
  byListing,
  factors,
}: {
  headingId: string;
  listingIds: string[];
  listingNames: Record<string, string>;
  byListing: Record<string, ModelRollupData>;
  factors: CrossListingFactor[];
}) {
  if (listingIds.length < 2) return null;
  return (
    <section className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3" aria-labelledby={headingId}>
      <h5 id={headingId} className="font-medium text-slate-800">Compared with your shortlisted homes</h5>
      <div className="mt-2 overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead><tr className="border-b border-slate-200 text-left"><th className="px-2 py-2">Factor</th>{listingIds.map((id) => <th key={id} className="px-2 py-2">{listingNames[id]}</th>)}</tr></thead>
          <tbody>
            {factors.map(([label, getScore]) => {
              const scores = listingIds.map((id) => getScore(byListing[id]));
              return <tr key={label} className="border-b border-slate-100"><td className="px-2 py-2 font-medium">{label}</td>{listingIds.map((id, index) => <td key={id} className="px-2 py-2">{comparisonPosition(scores[index], scores)}</td>)}</tr>;
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-slate-500">Positions use the underlying scores; equal results within a small rounding tolerance are shown as ties.</p>
    </section>
  );
}

function PublicTransportComparison({ listingIds, listingNames, byListing }: { listingIds: string[]; listingNames: Record<string, string>; byListing: Record<string, ModelRollupData> }) {
  const factors: CrossListingFactor[] = [
    ["Overall public transport", (rollup: ModelRollupData | undefined) => rollup?.unrounded_score ?? rollup?.display_score ?? null],
    ["Access", (rollup: ModelRollupData | undefined) => componentByName(rollup, "access")?.score ?? null],
    ["Bus coverage", (rollup: ModelRollupData | undefined) => componentByName(rollup, "bus_coverage")?.score ?? null],
    ["MRT reach", (rollup: ModelRollupData | undefined) => componentByName(rollup, "mrt_reach")?.score ?? null],
    ["Route resilience", (rollup: ModelRollupData | undefined) => componentByName(rollup, "route_resilience")?.score ?? null],
  ];
  return <ShortlistedHomesComparison headingId="transport-comparison-heading" listingIds={listingIds} listingNames={listingNames} byListing={byListing} factors={factors} />;
}

function ExpandableScoreRow({ title, component, children }: { title: string; component: ComponentResultData | null; children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const detailsId = useId();
  const score = displayScore(component?.score);
  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <button
        type="button"
        aria-expanded={isOpen}
        aria-controls={detailsId}
        aria-label={`${title} details`}
        onClick={() => setIsOpen((open) => !open)}
        className="flex w-full items-center gap-3 p-3 text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-teal-600"
      >
        <span className="font-medium">{title}</span>
        <span className="text-sm text-slate-600">{score.rating ?? "Not available"}</span>
        <span className="ml-auto text-sm font-semibold">{formatDisplayScore(score)}</span>
        <svg className={`h-4 w-4 shrink-0 text-teal-700 transition-transform ${isOpen ? "rotate-180" : ""}`} viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {isOpen && <div id={detailsId} className="space-y-3 border-t border-slate-100 p-3">{children}</div>}
    </section>
  );
}

function PublicTransportDetails({ rollup }: { rollup: ModelRollupData }) {
  const breakdownHeadingId = useId();
  const access = componentByName(rollup, "access");
  const bus = componentByName(rollup, "bus_coverage");
  const mrt = componentByName(rollup, "mrt_reach");
  const resilience = componentByName(rollup, "route_resilience");
  const accessValue = recordValue(access?.value);
  const selectedPath = recordValue(accessValue.selected_access_path);
  const accessEvidence = (access?.evidence ?? []).filter((item) => item.access_point_type || item.path_type);
  const mrtEvidence = recordValue(mrt?.evidence?.[0]);
  const busValue = recordValue(bus?.value);
  const resilienceValue = recordValue(resilience?.value);
  const fallbackExamples = Array.isArray(resilienceValue.fallback_examples) ? resilienceValue.fallback_examples : [];
  const busCount = numberValue(busValue.direct_corridors);
  const transferCount = numberValue(busValue.one_transfer_new_corridors);
  const stationBuckets = recordValue(mrtEvidence.reachable_station_summaries);
  const stationRows = [
    ["Reachable without changing trains", numberValue(mrtEvidence.zero_transfer_30)],
    ["Reachable with one train transfer", numberValue(mrtEvidence.one_transfer_30_incremental)],
    ["Reachable with additional transfers", numberValue(mrtEvidence.multi_transfer_30_incremental)],
  ];
  return (
    <section className="mt-3 rounded-lg border border-slate-200 bg-slate-50" aria-labelledby={breakdownHeadingId}>
      <h5 id={breakdownHeadingId} className="border-b border-slate-200 p-3 font-medium text-teal-800">Transport breakdown</h5>
      <div className="space-y-3 p-3">
        <ExpandableScoreRow title="Access" component={access}>
          <p className="text-sm text-slate-600">How easily can you get from the home to a useful bus stop or MRT station?</p>
          {access && <><p className="font-medium">Best network-entry option</p><p className="text-sm">{naturalAccessLabel(selectedPath)}</p><div className="overflow-x-auto"><table className="min-w-full text-sm"><tbody>{accessStages(selectedPath).map(([label, minutes, status]) => <tr key={label} className="border-b border-slate-100"><td className="py-2 pr-4">{label}</td><td className="py-2 text-right">{minutesText(minutes, status)}</td></tr>)}</tbody></table></div><p className="text-sm text-slate-700"><span className="font-medium">Why it is {ratingForScore(access.score)?.toLowerCase() ?? "unavailable"}:</span> {accessExplanation(access, selectedPath)}</p></>}
          {accessEvidence.length > 1 && <details><summary className="cursor-pointer text-sm text-teal-700">View alternative access options</summary><ul className="mt-2 space-y-1 text-sm">{accessEvidence.slice(0, 8).map((entry, index) => <li key={`${String(entry.bus_stop_code ?? entry.station_name ?? index)}-${index}`}>{naturalAccessLabel(entry)}{numberValue(entry.total_expected_minutes) != null ? ` — about ${Math.round(numberValue(entry.total_expected_minutes) as number)} min` : ""}</li>)}</ul></details>}
        </ExpandableScoreRow>
        <ExpandableScoreRow title="Bus coverage" component={bus}>
          <p className="text-sm text-slate-600">How many genuinely different areas can be reached using nearby bus services?</p>
          <ul className="space-y-1 text-sm"><li><span className="font-medium">What you get:</span> {busCount == null ? "Not available" : `${busCount} distinct direct bus corridor${busCount === 1 ? "" : "s"}`}</li><li>{transferCoverageLabel(transferCount) ?? "Additional coverage not available"}</li><li>Multiple directions of travel where scheduled services support them</li></ul>
          <p className="text-sm text-slate-700"><span className="font-medium">Why it scores {ratingForScore(bus?.score)?.toLowerCase() ?? "unavailable"}:</span> {bus?.strengths?.[0] ?? bus?.explanation ?? "Bus coverage evidence is unavailable."}</p>
          <p className="text-sm text-slate-600"><span className="font-medium">Limitation:</span> One-transfer coverage is estimated from scheduled bus-route structure; transfers are not guaranteed to be equally fast throughout the day.</p>
          {bus?.evidence?.length ? <details><summary className="cursor-pointer text-sm text-teal-700">View direct bus corridors</summary><ul className="mt-2 space-y-1 text-sm">{bus.evidence.map((corridor, index) => <li key={String(corridor.corridor_id ?? index)}>{Array.isArray(corridor.member_services) ? corridor.member_services.map((service) => recordValue(service).service).filter(Boolean).join(", ") : "Bus corridor"}{corridor.destination ? ` — ${String(corridor.destination)}` : ""}</li>)}</ul></details> : null}
        </ExpandableScoreRow>
        <ExpandableScoreRow title="MRT reach" component={mrt}>
          <p className="text-sm text-slate-600">How well connected are you to the rail network after reaching a practical MRT station?</p>
          <p className="font-medium">Best rail entry</p>
          <p className="text-sm">{String(mrtEvidence.primary_station_name ?? mrtEvidence.primary_physical_station_id ?? "Not available")}</p>
          <p className="text-sm text-slate-600">{Array.isArray(mrtEvidence.primary_station_lines) && mrtEvidence.primary_station_lines.length ? mrtEvidence.primary_station_lines.map((line) => lineName(String(line))).join(" · ") : "Rail lines not available"}</p>
          <div className="overflow-x-auto"><table className="min-w-full text-sm"><tbody>{stationRows.map(([label, count]) => <tr key={String(label)} className="border-b border-slate-100"><td className="py-2 pr-4">{label}</td><td className="py-2 text-right">{count == null ? "Not available" : count}</td></tr>)}</tbody></table></div>
          <p className="text-sm text-slate-700"><span className="font-medium">Why it scores {ratingForScore(mrt?.score)?.toLowerCase() ?? "unavailable"}:</span> {mrt?.explanation ?? "Rail reach evidence is unavailable."}</p>
          <p className="text-sm text-slate-600"><span className="font-medium">Limitation:</span> These estimates use rail-network structure and approximate travel times. Walking, waiting, feeder travel and station-entry time are measured separately under Access.</p>
          {Object.keys(stationBuckets).length > 0 && <details><summary className="cursor-pointer text-sm text-teal-700">View reachable MRT stations</summary><div className="mt-2 grid gap-2 md:grid-cols-2">{Object.entries(stationBuckets).map(([bucket, entries]) => <div key={bucket}><p className="font-medium capitalize">{bucket.replace(/_/g, " ")}</p><p className="text-sm text-slate-600">{Array.isArray(entries) ? entries.slice(0, 12).map((entry) => recordValue(entry).station_name).join(", ") : "Not available"}</p></div>)}</div></details>}
        </ExpandableScoreRow>
        <ExpandableScoreRow title="Route resilience" component={resilience}>
          <p className="text-sm text-slate-600">Do you have genuinely different alternatives when the usual route is inconvenient or unavailable?</p>
          <p className="font-medium">Available alternatives</p>
          <p className="text-sm">{fallbackExamples.length ? `${fallbackExamples.length} independent fallback option${fallbackExamples.length === 1 ? "" : "s"} found` : "No independent fallback options confirmed"}</p>
          {fallbackExamples.length > 0 && <ul className="space-y-1 text-sm">{fallbackExamples.slice(0, 6).map((example, index) => <li key={index}>{String(recordValue(example).label ?? "Alternative route")}{recordValue(example).detail ? ` — ${String(recordValue(example).detail)}` : ""}</li>)}</ul>}
          <p className="text-sm text-slate-700"><span className="font-medium">Why it scores {ratingForScore(resilience?.score)?.toLowerCase() ?? "unavailable"}:</span> {resilience?.explanation ?? "Resilience evidence is unavailable."}</p>
          <p className="text-sm text-slate-600"><span className="font-medium">Limitation:</span> This measures structural alternatives. It does not predict live train disruptions, road congestion or temporary service problems.</p>
        </ExpandableScoreRow>
      </div>
    </section>
  );
}

function PublicTransportPanel({ listingIds, listingNames, byListing }: { listingIds: string[]; listingNames: Record<string, string>; byListing: Record<string, ModelRollupData> }) {
  return <div className="mt-3 space-y-3">{listingIds.map((id) => {
    const rollup = byListing[id];
    if (!rollup || !rollup.components?.length) return <div key={id} className="rounded-lg border border-amber-200 bg-amber-50 p-3"><h4 className="font-medium">{listingNames[id]}</h4><p className="mt-1 text-sm text-amber-800">Public transport is not assessed yet.</p></div>;
    const overall = displayScore(rollup.display_score);
    return <article key={id} className="rounded-lg border border-slate-200 bg-white p-4" aria-labelledby={`transport-${id}`}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h4 id={`transport-${id}`} className="font-semibold">{listingNames[id]}</h4>
        <ScoreRing score={overall.rawScore} label="Transport strength" size="sm" partial={!rollup.is_complete} statusLabel={rollup.is_complete ? "Complete" : "Partial"} />
      </div>
      <p className="mt-1 text-sm font-medium text-slate-800">{buildTransportHeadline(rollup)}</p>
      <p className="mt-2 text-sm text-slate-600">{buildTransportExplanation(rollup)}</p>
      <PublicTransportDetails rollup={rollup} />
    </article>;
  })}<PublicTransportComparison listingIds={listingIds} listingNames={listingNames} byListing={byListing} /></div>;
}

const drivingFactorDefinitions = [
  ["Major-road access", "major_road_access", "How quickly can you reach a useful expressway or major road from the home?", "This measures the traffic-aware driving time from the listing to nearby expressway or major-road entry points."],
  ["Route flexibility", "route_connectivity", "Do you have alternative routes when one road is congested or disrupted?", "This considers the number of distinct major roads reached and whether the available routes meaningfully differ from one another."],
  ["Peak-hour access reliability", "peak_access_penalty", "How consistent is access to the road network during peak hours?", "This compares the time needed to reach the selected major-road entry during peak and off-peak periods."],
  ["Parking convenience", "parking_convenience", "How easy is it to park near the home and walk back to the block?", "This considers walking distance to the likely nearby HDB carpark, nearby alternatives, shelter, parking restrictions and night-parking availability."],
] as const;

function DrivingParkingCard({ component, idSuffix }: { component: ComponentResultData | null; idSuffix: string }) {
  const primary = parkingPrimary(component);
  if (!primary) return <p className="text-sm text-slate-600">No likely nearby official HDB carpark was confirmed.</p>;
  const availability = asRecord(primary.availability);
  const snapshot = availabilitySnapshot(availability);
  const facts = parkingFacts(component);
  const value = asRecord(component?.value);
  const alternatives250 = typeof value?.reasonable_carparks_within_250m === "number" ? value.reasonable_carparks_within_250m : null;
  const gantry = typeof primary.gantry_height_m === "number" ? primary.gantry_height_m : null;
  const shortTerm = String(primary.short_term_parking ?? "").toUpperCase();
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-3" aria-labelledby={`parking-card-heading-${idSuffix}`}>
      <h5 id={`parking-card-heading-${idSuffix}`} className="font-medium">Parking</h5>
      <p className="mt-2 text-sm font-medium">Likely nearby carpark</p>
      <p className="mt-1 break-words text-sm">{String(primary.address ?? "Official HDB carpark")}</p>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-700">
        {facts.filter((fact) => !fact.includes("nearby carpark option")).map((fact) => <li key={fact}>{fact}</li>)}
      </ul>
      {alternatives250 != null && <p className="mt-1 text-sm text-slate-700">{alternatives250} practical alternative{alternatives250 === 1 ? "" : "s"} within 250 metres</p>}
      {gantry != null && <p className="mt-1 text-sm text-slate-700">Gantry height: {gantry} m</p>}
      {shortTerm && shortTerm !== "UNKNOWN" && <p className="mt-1 text-sm text-slate-700">Short-term parking: {String(primary.short_term_parking)}</p>}
      <div className="mt-4 border-t border-slate-100 pt-3" aria-labelledby={`availability-heading-${idSuffix}`}>
        <h6 id={`availability-heading-${idSuffix}`} className="font-medium">Current availability snapshot</h6>
        {snapshot.countText ? <p className="mt-1 text-sm">{snapshot.countText}</p> : null}
        <p className={`mt-1 text-sm font-medium ${snapshot.state === "stale" || snapshot.state === "unavailable" ? "text-amber-800" : "text-slate-800"}`}>{snapshot.label}</p>
        {snapshot.reportedAt && <p className="mt-1 text-xs text-slate-600">Last reported: {snapshot.reportedAt}</p>}
        <p className="mt-1 text-xs text-slate-600">{snapshot.explanation}</p>
      </div>
    </section>
  );
}

function drivingComponentResult(component: ComponentResultData | null): string | null {
  if (component?.name === "major_road_access") return roadResult(component);
  if (component?.name === "route_connectivity") return routeResult(component);
  if (component?.name === "peak_access_penalty") return peakResult(component);
  if (component?.name === "parking_convenience") {
    const primary = parkingPrimary(component);
    const value = asRecord(component.value) ?? {};
    const walk = primary?.walk_minutes;
    const alternatives = value.reasonable_carparks_within_500m;
    return primary && typeof walk === "number"
      ? `The likely nearby carpark is approximately ${Math.max(1, Math.round(walk))} minute${Math.round(walk) === 1 ? "" : "s"} away on foot${typeof alternatives === "number" ? `, with ${alternatives} practical alternative${alternatives === 1 ? "" : "s"} within 500 metres` : ""}.`
      : null;
  }
  return null;
}

function DrivingFactorRow({ name, component, idSuffix }: { name: string; component: ComponentResultData | null; idSuffix: string }) {
  const definition = drivingFactorDefinitions.find((item) => item[1] === name);
  if (!definition) return null;
  const [title, , question, explanation] = definition;
  const result = drivingComponentResult(component);
  const source = component?.source ? readableSource(component.source) : "Source unavailable";
  return (
    <ExpandableScoreRow title={title} component={component}>
      <section>
        <h6 className="text-sm font-medium">What it means</h6>
        <p className="mt-1 text-sm text-slate-600">{question}</p>
        <p className="mt-1 text-xs text-slate-500">{explanation}</p>
        {component?.name === "peak_access_penalty" && <p className="mt-1 text-xs text-slate-500">This measures access to the wider road network, not the duration of a personal commute. Regular destination journeys are shown separately.</p>}
      </section>
      <section>
        <h6 className="text-sm font-medium">Your result</h6>
        <p className="mt-1 text-sm text-slate-800">{result ?? component?.explanation ?? "This component could not be assessed from the available evidence."}</p>
      </section>
      <section>
        <h6 className="text-sm font-medium">Supporting details</h6>
        <p className="mt-1 text-xs text-slate-500">{source} · {component?.confidence ? `${humanizeLabel(component.confidence)} confidence` : "Confidence unavailable"}</p>
        {component?.name === "parking_convenience" && <>
          <p className="mt-1 text-xs text-slate-500">Live available-lot data is a current snapshot and does not affect this score until sufficient historical observations are available.</p>
          <div className="mt-3"><DrivingParkingCard component={component} idSuffix={idSuffix} /></div>
        </>}
      </section>
    </ExpandableScoreRow>
  );
}

function DrivingBreakdown({ rollup, idSuffix }: { rollup: ModelRollupData; idSuffix: string }) {
  const headingId = useId();
  return (
    <section className="mt-4 rounded-lg border border-slate-200 bg-slate-50" aria-labelledby={headingId}>
      <h5 id={headingId} className="border-b border-slate-200 p-3 font-medium text-teal-800">Driving breakdown</h5>
      <div className="space-y-3 p-3">
        {drivingFactorDefinitions.map(([, name]) => <DrivingFactorRow key={name} name={name} idSuffix={idSuffix} component={drivingComponent(rollup, name)} />)}
      </div>
    </section>
  );
}

function DrivingConnectivityComparison({ listingIds, listingNames, byListing }: { listingIds: string[]; listingNames: Record<string, string>; byListing: Record<string, ModelRollupData> }) {
  const factors: CrossListingFactor[] = [
    ["Overall driving connectivity", (rollup) => rollup?.unrounded_score ?? rollup?.display_score ?? null],
    ["Major-road access", (rollup) => drivingComponent(rollup, "major_road_access")?.score ?? null],
    ["Route flexibility", (rollup) => drivingComponent(rollup, "route_connectivity")?.score ?? null],
    ["Peak-hour access reliability", (rollup) => drivingComponent(rollup, "peak_access_penalty")?.score ?? null],
    ["Parking convenience", (rollup) => drivingComponent(rollup, "parking_convenience")?.score ?? null],
  ];
  return <ShortlistedHomesComparison headingId="driving-comparison-heading" listingIds={listingIds} listingNames={listingNames} byListing={byListing} factors={factors} />;
}

function DrivingPanel({ listingIds, listingNames, byListing, hasDestination }: { listingIds: string[]; listingNames: Record<string, string>; byListing: Record<string, ModelRollupData>; hasDestination: boolean }) {
  return <div className="mt-3 space-y-3">{listingIds.map((id) => {
    const rollup = byListing[id];
    if (!rollup || !rollup.components?.length) return <article key={id} className="rounded-lg border border-amber-200 bg-amber-50 p-3"><h4 className="font-medium">{listingNames[id]}</h4><p className="mt-1 text-sm text-amber-900">Driving connectivity: Unavailable</p><p className="mt-1 text-sm text-amber-800">Run enrichment to calculate this.</p></article>;
    const status = drivingStatus(rollup);
    const score = displayDrivingScore(rollup.unrounded_score ?? rollup.display_score);
    return <article key={id} className="rounded-lg border border-slate-200 bg-white p-4" aria-labelledby={`driving-${id}`}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h4 id={`driving-${id}`} className="font-semibold">{listingNames[id]}</h4>
        <ScoreRing score={status === "Unavailable" ? null : score.rawScore} label="Driving connectivity" size="sm" partial={status === "Provisional"} statusLabel={status} />
      </div>
      <p className="mt-1 text-sm font-medium text-slate-800">{drivingHeadline(rollup)}</p>
      <p className="mt-2 text-sm text-slate-600">{drivingExplanation(rollup)}</p>
      {status === "Provisional" && <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"><p className="font-medium">This score is provisional because one or more general driving components could not be assessed.</p></div>}
      <DrivingBreakdown rollup={rollup} idSuffix={id} />
      {status === "Provisional" && <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"><p className="font-medium">Why the score is provisional</p><p className="mt-1">{unavailableDrivingComponentNames(rollup).map((name) => { const component = drivingComponent(rollup, name); return `${name.replace(/_/g, " ")} normally contributes ${Math.round((component?.weight ?? 0) * 100)}%`; }).join("; ")} is unavailable. NearHome calculated the displayed score using the available components and temporarily reweighted them for comparison.</p></div>}
      {!hasDestination && <a className="text-sm font-medium text-teal-700 underline" href="#buyer-profile">Add a regular destination to compare journey times</a>}
    </article>;
  })}<DrivingConnectivityComparison listingIds={listingIds} listingNames={listingNames} byListing={byListing} /></div>;
}

function RegularDestinationJourneyPanel({
  journeys,
  listingNames,
}: {
  journeys: ComparisonResponse["regular_destination_journeys"];
  listingNames: Record<string, string>;
}) {
  if (!journeys.length) return null;
  return (
    <section className="nh-card" aria-labelledby="regular-destination-heading">
      <h3 id="regular-destination-heading" className="text-lg font-semibold">Regular Destination Journey</h3>
      <p className="mt-1 text-xs text-slate-500">Estimated personal driving journeys are shown separately and do not change Driving Connectivity.</p>
      <div className="mt-3 space-y-2">
        {journeys.map((journey) => {
          const peers = journeys.filter((item) => item.important_location_id === journey.important_location_id && item.status === "AVAILABLE" && item.duration_minutes != null).sort((a, b) => (a.duration_minutes ?? Infinity) - (b.duration_minutes ?? Infinity));
          const rank = journey.duration_minutes == null ? null : peers.findIndex((item) => item.listing_id === journey.listing_id) + 1;
          const available = journey.status === "AVAILABLE" && journey.duration_minutes != null;
          return (
            <article key={journey.journey_estimate_id} className="rounded-lg border border-slate-200 p-3 text-sm">
              <p className="font-medium">{journey.destination_label} · {listingNames[journey.listing_id] ?? journey.listing_id.slice(0, 8)}</p>
              {journey.destination_address && <p className="mt-1 text-xs text-slate-500">{journey.destination_address}</p>}
              <p className="mt-2 text-slate-700">{journey.selected_day_type.toLowerCase()} at {journey.selected_time_local.slice(0, 5)}</p>
              {available ? (
                <>
                  <p className="mt-1 text-lg font-semibold">Estimated {journey.duration_minutes} min drive</p>
                  {rank != null && <p className="mt-1 text-slate-600">{rank === 1 ? "Fastest" : `${rank}th fastest`} among {peers.length} shortlisted homes</p>}
                </>
              ) : (
                <p className="mt-1 rounded bg-amber-50 p-2 text-amber-800">This destination journey could not be confirmed. Driving Connectivity remains independent of this failure.</p>
              )}
              <p className="mt-2 text-xs text-slate-500">Data source: {readableSource(journey.source)} · Confidence: {humanizeLabel(journey.confidence)}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function ComparisonView({ data, session }: Props) {
  const listingIds = [...new Set(data.immediate_metrics.map((m) => m.listing_id))];
  const listingNames = Object.fromEntries(
    (session?.listings ?? []).map((l) => [l.listing_id, l.display_name]),
  );
  const listingsById = Object.fromEntries(
    (session?.listings ?? []).map((listing) => [listing.listing_id, listing]),
  );

  const metricRows = [
    "asking_price",
    "budget_difference",
    "floor_area_sqm",
    "price_per_sqm",
    "remaining_lease_years",
  ];

  const byListingMetric = (listingId: string, metric: string) =>
    data.immediate_metrics.find((m) => m.listing_id === listingId && m.metric_name === metric);

  const enrichmentFallback = (listingId: string, enrichmentType: string) => {
    const status = data.enrichment_summary.find(
      (run) => run.listing_id === listingId && run.enrichment_type === enrichmentType,
    )?.status;
    if (status === "FAILED" || status === "ERROR") return "Unavailable";
    if (status === "RUNNING" || status === "QUEUED") return "Still calculating";
    return "Unavailable";
  };

  const valuationSummary = (listingId: string) => {
    const fairPrice = data.fair_price_by_listing[listingId];
    const estimate = formatFiniteCurrency(fairPrice?.central_estimate);
    if (!fairPrice || fairPrice.status === "INSUFFICIENT_EVIDENCE" || !estimate) {
      return <span className="text-amber-800">Estimate unavailable</span>;
    }
    const askingPrice = resolveAskingPrice(listingsById[listingId], fairPrice);
    const difference = formatValuationDifference(askingPrice, fairPrice.central_estimate);
    const assessment = getPriceAssessment(difference.percentage);
    return (
      <div className="space-y-0.5">
        <p className="font-medium text-slate-800">{difference.label}</p>
        <p className="text-xs text-slate-600">{assessment?.label ?? "Assessment unavailable"} · {estimate} · {fairPrice.confidence ? `${humanizeLabel(fairPrice.confidence)} confidence` : "Confidence unavailable"}</p>
      </div>
    );
  };

  const transportSummary = (listingId: string) => {
    const rollup = data.transport_by_listing[listingId];
    const score = displayScore(rollup?.unrounded_score ?? rollup?.display_score);
    if (!rollup || score.roundedScore === null) return <span className="text-amber-800">{enrichmentFallback(listingId, "PUBLIC_TRANSPORT")}</span>;
    return (
      <div className="space-y-0.5">
        <p className="font-medium text-slate-800">{formatDisplayScore(score)} · {score.rating ?? "Not assessed"}</p>
        <p className={`text-xs ${rollup.is_complete ? "text-slate-600" : "text-amber-800"}`}>{rollup.is_complete ? "Assessed" : "Partial result"}</p>
      </div>
    );
  };

  const drivingSummary = (listingId: string) => {
    const rollup = data.driving_by_listing[listingId];
    const score = displayDrivingScore(rollup?.unrounded_score ?? rollup?.display_score);
    if (!rollup || score.roundedScore === null) return <span className="text-amber-800">{enrichmentFallback(listingId, "DRIVING_ACCESS")}</span>;
    return (
      <div className="space-y-0.5">
        <p className="font-medium text-slate-800">{formatDrivingScore(score)} · {score.rating ?? "Not assessed"}</p>
        <p className={`text-xs ${rollup.is_complete ? "text-slate-600" : "text-amber-800"}`}>{rollup.is_complete ? "Assessed" : "Partial result"}</p>
      </div>
    );
  };

  const rec = data.recommendation;
  const showTransport = ["MAINLY_PUBLIC_TRANSPORT", "BOTH"].includes(
    session?.buyer_profile?.main_transport_mode ?? "",
  );
  const showDriving = ["MAINLY_DRIVING", "BOTH"].includes(
    session?.buyer_profile?.main_transport_mode ?? "",
  );
  const publicTransitJourneys = data.journey_results.filter((journey) => journey.mode !== "DRIVING");
  const hasJourneys = publicTransitJourneys.length > 0;
  const hasDrivingDestination = (session?.buyer_profile?.important_locations ?? []).some((location) =>
    location.is_complete !== false && ["DRIVING", "BOTH"].includes(location.transport_mode ?? ""),
  );
  const showSchools = session?.buyer_profile?.schools_matter === true;
  const observations = data.observations ?? [];
  const recommendationId = rec?.recommended_listing_id;

  return (
    <div className="space-y-6">
      <section className="nh-card border-blue-200 bg-gradient-to-br from-white to-blue-50/70" aria-labelledby="decision-overview-heading">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="nh-section-kicker">Comparison overview</p>
            <h3 id="decision-overview-heading" className="mt-1 text-2xl font-bold tracking-tight text-blue-950">Your comparison is ready</h3>
            <p className="mt-1 max-w-2xl text-sm text-slate-600">Review the factor-level evidence and the trade-offs most relevant to your selected priorities.</p>
          </div>
          {rec?.is_provisional && <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-800">Provisional recommendation</span>}
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {listingIds.map((id) => {
            const isRecommended = recommendationId === id;
            return (
              <article key={id} className={`rounded-xl border p-4 ${isRecommended ? "border-emerald-500 bg-white shadow-sm ring-1 ring-emerald-200" : "border-slate-200 bg-white"}`}>
                <div className="min-w-0">
                  <h4 className="truncate font-semibold text-slate-900">{listingNames[id] ?? id.slice(0, 8)}</h4>
                  {isRecommended && <span className="mt-2 inline-flex rounded-full bg-emerald-100 px-2 py-1 text-xs font-semibold text-emerald-800">Leading option</span>}
                </div>
                {isRecommended && rec?.one_sentence_summary && <p className="mt-3 text-sm text-slate-700">{rec.one_sentence_summary}</p>}
                {!isRecommended && rec?.why_not_selected?.[id] && <p className="mt-3 text-sm text-slate-600">{rec.why_not_selected[id]}</p>}
              </article>
            );
          })}
        </div>
      </section>
      {rec && (
        <section className="nh-card border-teal-300 bg-teal-50/40" aria-labelledby="rec-heading">
          <p className="nh-section-kicker">Recommendation rationale</p>
          <h3 id="rec-heading" className="mt-1 text-lg font-semibold text-teal-900">Why this result?</h3>
          <p className="mt-2 text-slate-800">{rec.one_sentence_summary}</p>
          <p className="mt-1 text-sm text-slate-600">
            Confidence: {humanizeLabel(rec.confidence)}
            {rec.confidence_reasons.length > 0 && ` — ${rec.confidence_reasons.join("; ")}`}
          </p>
        </section>
      )}

      <section className="nh-expanded-panel" aria-labelledby="price-heading">
        <p className="nh-section-kicker">Quick comparison</p>
        <h3 id="price-heading" className="mt-1 text-lg font-semibold">Price and affordability</h3>
        <p className="mt-1 text-sm text-slate-600">Start with the asking price, budget position, space and remaining lease.</p>
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left">
                <th className="sticky left-0 bg-teal-50/80 px-2 py-2 font-medium">Metric</th>
                {listingIds.map((id) => (
                  <th key={id} className="px-3 py-2 font-medium">
                    {listingNames[id] ?? id.slice(0, 8)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metricRows.map((metric) => (
                <tr key={metric} className="border-b border-slate-100">
                  <td className="sticky left-0 bg-teal-50/80 px-2 py-2 font-medium capitalize text-slate-700">
                    {humanizeLabel(metric)}
                  </td>
                  {listingIds.map((id) => {
                    const cell = byListingMetric(id, metric);
                    const val = cell?.raw_value;
                    let display: string;
                    let displayClass = "";
                    if (metric === "budget_difference") {
                      display = formatBudgetDifference(val);
                      displayClass = budgetDifferenceClass(val);
                    } else if (metric.includes("price")) {
                      display = formatCurrency(val);
                    } else if (val === null || val === undefined) {
                      display = "—";
                    } else {
                      display = String(val);
                    }
                    return (
                      <td key={id} className={`px-3 py-2 ${displayClass}`}>
                        {display}
                        {cell && cell.status !== "AVAILABLE" && (
                          <span className="ml-1 text-xs text-amber-700">({cell.status})</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr className="border-b border-slate-100 align-top">
                <td className="sticky left-0 bg-teal-50/80 px-2 py-3 font-medium text-slate-700">Asking Price vs Estimated Value</td>
                {listingIds.map((id) => <td key={id} className="min-w-56 px-3 py-3">{valuationSummary(id)}</td>)}
              </tr>
              <tr className="border-b border-slate-100 align-top">
                <td className="sticky left-0 bg-teal-50/80 px-2 py-3 font-medium text-slate-700">Public Transport Strength</td>
                {listingIds.map((id) => <td key={id} className="min-w-56 px-3 py-3">{transportSummary(id)}</td>)}
              </tr>
              <tr className="border-b border-slate-100 align-top">
                <td className="sticky left-0 bg-teal-50/80 px-2 py-3 font-medium text-slate-700">Driving Connectivity</td>
                {listingIds.map((id) => <td key={id} className="min-w-56 px-3 py-3">{drivingSummary(id)}</td>)}
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="nh-expanded-panel" aria-labelledby="fair-price-heading">
        <p className="nh-section-kicker">Market evidence</p>
        <h3 id="fair-price-heading" className="mt-1 text-lg font-semibold">Fair-price estimate</h3>
        <p className="mt-1 text-sm text-slate-600">An analytical estimate based on comparable HDB transactions, not an official valuation.</p>
        <FairPricePanel
          listingIds={listingIds}
          listingNames={listingNames}
          listings={session?.listings ?? []}
          fairPriceByListing={data.fair_price_by_listing}
          status={data.fair_price_status}
        />
      </section>

      {showTransport && (
        <section className="nh-card" aria-labelledby="pt-heading">
          <p className="nh-section-kicker">General connectivity</p>
          <h3 id="pt-heading" className="mt-1 text-lg font-semibold">Public transport strength</h3>
          <p className="text-xs text-slate-500">General connectivity — excludes personalised journey duration</p>
          <PublicTransportPanel
            listingIds={listingIds}
            listingNames={listingNames}
            byListing={data.transport_by_listing}
          />
        </section>
      )}

      {!showDriving && session?.buyer_profile?.main_transport_mode === "MAINLY_PUBLIC_TRANSPORT" && (
        <section className="nh-card border-dashed" aria-labelledby="driving-hidden-heading">
          <h3 id="driving-hidden-heading" className="text-sm font-medium text-slate-700">
            Driving connectivity
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            Not shown because your profile uses public transport only. Set transport mode to{" "}
            <span className="font-medium">Both</span> or <span className="font-medium">Mainly driving</span> in your
            buyer profile, save, then run enrichment again.
          </p>
        </section>
      )}

      {showDriving && (
        <section className="nh-card" aria-labelledby="driving-heading">
          <p className="nh-section-kicker">General connectivity</p>
          <h3 id="driving-heading" className="mt-1 text-lg font-semibold">Driving Connectivity</h3>
          <p className="text-xs text-slate-500">How convenient, flexible and reliable driving is from each home generally. Regular destination journeys are assessed separately.</p>
          <DrivingPanel listingIds={listingIds} listingNames={listingNames} byListing={data.driving_by_listing} hasDestination={hasDrivingDestination} />
        </section>
      )}

      {showDriving && <RegularDestinationJourneyPanel journeys={data.regular_destination_journeys ?? []} listingNames={listingNames} />}

      {hasJourneys && (
        <section className="nh-card" aria-labelledby="journey-heading">
          <p className="nh-section-kicker">Personal context</p>
          <h3 id="journey-heading" className="mt-1 text-lg font-semibold">Your journeys</h3>
          <p className="mt-1 text-xs text-slate-500">Personal journeys are shown separately from the general public-transport score.</p>
          <div className="mt-3 space-y-2">
            {publicTransitJourneys.map((j, idx) => (
              <div key={idx} className="rounded-lg border border-slate-200 p-3 text-sm">
                <span className="font-medium">{session?.buyer_profile?.important_locations?.find((location) => location.important_location_id === j.important_location_id)?.label ?? "Important location"}</span>
                {" · "}
                <span className="font-medium">{listingNames[j.listing_id] ?? j.listing_id.slice(0, 8)}</span>
                {" · "}
                {j.mode.replace(/_/g, " ").toLowerCase()}
                {" · "}
                {j.requested_day_type.toLowerCase()} at {j.requested_time_local.slice(0, 5)}
                {j.status === "AVAILABLE" && j.duration_minutes != null ? (
                  <span>
                    {" — "}
                    {j.is_fastest ? "Fastest" : `${j.duration_minutes} min`}
                    {j.difference_from_fastest_seconds != null && j.difference_from_fastest_seconds > 0 && (
                      <span> ({Math.round(j.difference_from_fastest_seconds / 60)} min longer than fastest)</span>
                    )}
                  </span>
                ) : (
                  <span className="text-amber-700"> — Journey estimate unavailable for this transport mode.</span>
                )}
                <span className="ml-2 text-xs text-slate-400">(Data source: {readableSource(j.provider)})</span>
                <p className="mt-1 text-xs text-slate-500">Detailed walking, waiting and transfer stages are not available for this stored journey result.</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {showSchools && (
        <NearbySchoolsPanel
          listingIds={listingIds}
          listingNames={listingNames}
          byListing={data.schools_by_listing ?? {}}
          selectedSchools={session?.buyer_profile?.named_schools?.length ? session.buyer_profile.named_schools : session?.buyer_profile?.named_school ? [session.buyer_profile.named_school] : []}
        />
      )}

      {observations.length > 0 && (
        <section className="nh-card" aria-labelledby="obs-heading">
          <h3 id="obs-heading" className="text-lg font-semibold">
            Your observations
          </h3>
          <p className="text-xs text-slate-500">Unverified notes — shown as context and not used in scoring.</p>
          <ul className="mt-2 space-y-2 text-sm">
            {observations.map((o) => (
              <li key={o.observation_id} className="rounded border border-slate-200 p-2">
                <span className="font-medium">{listingNames[o.listing_id] ?? o.listing_id.slice(0, 8)}</span>
                {" · "}
                <span className="text-slate-600">{o.category}: {o.value_text}</span>
                <span className="ml-2 text-xs text-amber-700">({o.verification_state})</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
