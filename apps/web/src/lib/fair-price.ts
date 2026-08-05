import type { FairPriceComparable, FairPriceData, SessionListing } from "./api";

/** Initial product-interpretation bands; these are not model-confidence thresholds. */
export const PRICE_ASSESSMENT_BANDS = {
  close: 2,
  slight: 5,
  ordinary: 10,
} as const;

export const SIMILARITY_LABEL_BANDS = {
  strong: 0.2,
  good: 0.1,
  moderate: 0.05,
} as const;

export type PriceDifferenceKind = "above" | "below" | "matches" | "unavailable";

export interface ValuationDifference {
  kind: PriceDifferenceKind;
  amount: number | null;
  percentage: number | null;
  label: string;
}

export interface PriceAssessment {
  label: string;
  explanation: string;
}

export interface ValuationLimitation {
  title: string;
  description: string;
}

function finiteNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value;
}

function positiveNumber(value: unknown): number | null {
  const number = finiteNumber(value);
  return number !== null && number > 0 ? number : null;
}

function formatMoney(value: number): string {
  return `S$${Math.round(value).toLocaleString("en-SG")}`;
}

function formatPercent(value: number): string {
  return `${Math.abs(value).toFixed(1)}%`;
}

/**
 * The percentage uses the central estimate as its denominator. Asking price is
 * described relative to the estimate so signs and wording cannot conflict.
 */
export function formatValuationDifference(
  askingPrice: number | null | undefined,
  estimate: number | null | undefined,
): ValuationDifference {
  const asking = positiveNumber(askingPrice);
  const central = positiveNumber(estimate);
  if (asking === null || central === null) {
    return { kind: "unavailable", amount: null, percentage: null, label: "Asking price unavailable" };
  }

  const difference = asking - central;
  if (Math.abs(difference) < 0.5) {
    return { kind: "matches", amount: 0, percentage: 0, label: "Matches estimate" };
  }

  const percentage = (difference / central) * 100;
  const direction = difference > 0 ? "above" : "below";
  return {
    kind: difference > 0 ? "above" : "below",
    amount: Math.round(Math.abs(difference)),
    percentage,
    label: `${formatMoney(Math.abs(difference))} ${direction} estimate · ${formatPercent(percentage)}`,
  };
}

export function resolveAskingPrice(
  listing: SessionListing | undefined,
  fairPrice: FairPriceData,
): number | null {
  const direct = positiveNumber(listing?.asking_price);
  if (direct !== null) return direct;

  const central = positiveNumber(fairPrice.central_estimate);
  const difference = finiteNumber(fairPrice.asking_difference_dollars);
  return central !== null && difference !== null ? central + difference : null;
}

/** These are product labels only; they do not claim that a listing is overpriced or underpriced. */
export function getPriceAssessment(percentage: number | null): PriceAssessment | null {
  if (percentage === null || !Number.isFinite(percentage)) return null;
  const absolute = Math.abs(percentage);
  if (absolute <= PRICE_ASSESSMENT_BANDS.close) {
    return {
      label: "Close to estimated market value",
      explanation: "The asking price is close to NearHome’s estimate; the likely range should also be considered.",
    };
  }

  const above = percentage > 0;
  if (absolute <= PRICE_ASSESSMENT_BANDS.slight) {
    return {
      label: above ? "Slightly above estimate" : "Slightly below estimate",
      explanation: above
        ? "The asking price is slightly above NearHome’s estimate, but this difference alone does not establish overpricing."
        : "The asking price is slightly below NearHome’s estimate, but the estimate remains subject to its likely range.",
    };
  }
  if (absolute <= PRICE_ASSESSMENT_BANDS.ordinary) {
    return {
      label: above ? "Above estimate" : "Below estimate",
      explanation: above
        ? "The asking price is above NearHome’s estimate; compare it with the likely range and supporting sales."
        : "The asking price is below NearHome’s estimate; confirm the listing details and supporting sales before drawing conclusions.",
    };
  }
  return {
    label: above ? "Significantly above estimate" : "Significantly below estimate",
    explanation: above
      ? "The asking price is substantially above NearHome’s estimate, although the estimate still has uncertainty."
      : "The asking price is substantially below NearHome’s estimate, although the estimate still has uncertainty.",
  };
}

export function buildPriceExplanation(
  askingPrice: number | null,
  fairPrice: FairPriceData,
  difference: ValuationDifference,
): string | null {
  const asking = positiveNumber(askingPrice);
  const low = positiveNumber(fairPrice.range_low);
  const high = positiveNumber(fairPrice.range_high);
  if (asking === null || difference.kind === "unavailable") return null;
  if (difference.kind === "matches") return "The asking price matches NearHome’s central estimate.";
  if (low !== null && high !== null && asking >= low && asking <= high) {
    return "The asking price differs from the central estimate but remains within NearHome’s likely market-value range.";
  }
  if (low !== null && asking < low) {
    return "The asking price is below NearHome’s likely market-value range.";
  }
  if (high !== null && asking > high) {
    return "The asking price is above NearHome’s likely market-value range.";
  }
  return difference.kind === "above"
    ? "The asking price is above NearHome’s central estimate."
    : "The asking price is below NearHome’s central estimate.";
}

function evidenceNumber(fairPrice: FairPriceData, key: string): number | null {
  const value = fairPrice.comparable_evidence?.[key];
  return finiteNumber(value);
}

function filterStatus(fairPrice: FairPriceData, key: string): Record<string, unknown> | null {
  const value = fairPrice.filter_status?.[key];
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function statusIs(fairPrice: FairPriceData, key: string, expected: string): boolean {
  return filterStatus(fairPrice, key)?.status === expected;
}

function friendlyFlatType(value: string | null | undefined): string | null {
  if (!value?.trim()) return null;
  return value.trim().replace(/\s+ROOM\b/i, "-room").replace(/\s+/g, " ");
}

function joinNatural(values: string[]): string {
  if (values.length <= 1) return values[0] ?? "relevant property details";
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(", ")}, and ${values[values.length - 1]}`;
}

export function buildFilterSummary(fairPrice: FairPriceData, listing: SessionListing | undefined): string {
  const town = fairPrice.town?.trim();
  const model = fairPrice.flat_model_used?.trim() || listing?.flat_model?.trim();
  const flatType = friendlyFlatType(listing?.flat_type);
  const descriptor = [town, model, flatType].filter((value): value is string => Boolean(value));
  const subject = descriptor.length > 0 ? descriptor.join(" ") : "similar HDB";
  const criteria: string[] = [];

  if (statusIs(fairPrice, "flat_type", "applied")) criteria.push("the same flat type");
  if (statusIs(fairPrice, "flat_model", "applied")) criteria.push("the same flat model");
  if (statusIs(fairPrice, "area_band", "applied")) criteria.push("similar floor area");
  if (statusIs(fairPrice, "lease_band", "applied")) criteria.push("similar remaining lease");
  if (statusIs(fairPrice, "storey_range", "applied")) criteria.push("similar storey information");

  const count = evidenceNumber(fairPrice, "eligible_comparable_count");
  const countText = count !== null ? `${Math.round(count)} relevant transaction${count === 1 ? "" : "s"}` : "recent transactions";
  const criteriaText = criteria.length > 0 ? ` with ${joinNatural(criteria)}` : "";
  let summary = `NearHome compared this listing with ${countText} for ${subject}${criteriaText}.`;

  const relaxationSteps = fairPrice.filter_status?.relaxation_steps;
  if (Array.isArray(relaxationSteps) && relaxationSteps.length > 0) {
    summary += ` To find enough evidence, the search was widened: ${relaxationSteps.join(" ")}`;
  } else if (fairPrice.filter_status) {
    summary += " No wider search was required.";
  }

  const omitted: string[] = [];
  if (statusIs(fairPrice, "town", "omitted_missing")) omitted.push("town");
  if (statusIs(fairPrice, "flat_model", "omitted_missing")) omitted.push("flat-model");
  if (statusIs(fairPrice, "storey_range", "omitted_missing")) omitted.push("storey");
  if (omitted.length > 0) {
    summary += ` Matching for ${joinNatural(omitted)} information was omitted because it was unavailable.`;
  }
  return summary;
}

function missingFieldNames(fairPrice: FairPriceData): string[] {
  const missing: string[] = [];
  if (statusIs(fairPrice, "storey_range", "omitted_missing")) missing.push("storey information");
  if (statusIs(fairPrice, "flat_model", "omitted_missing")) missing.push("flat model");
  if (statusIs(fairPrice, "town", "omitted_missing")) missing.push("town");
  return missing;
}

export function buildConfidenceExplanation(fairPrice: FairPriceData): string {
  const confidence = fairPrice.confidence?.toUpperCase();
  const count = evidenceNumber(fairPrice, "eligible_comparable_count");
  const missing = missingFieldNames(fairPrice);
  const wideSpread = fairPrice.confidence_reasons?.some((reason) => /price spread is wide/i.test(reason)) ?? false;
  const relaxed = Array.isArray(fairPrice.filter_status?.relaxation_steps) && fairPrice.filter_status.relaxation_steps.length > 0;
  const evidenceText = count !== null
    ? `${Math.round(count)} relevant transaction${count === 1 ? " was" : "s were"} available`
    : "relevant transaction evidence was available";
  const caveats = [
    missing.length > 0 ? `${joinNatural(missing)} ${missing.length === 1 ? "was" : "were"} missing` : null,
    wideSpread ? "comparable sale prices varied widely" : null,
    relaxed ? "the search needed to be widened" : null,
  ].filter((value): value is string => Boolean(value));

  if (confidence === "HIGH") {
    return caveats.length > 0
      ? `Many closely matched transactions were available, although ${joinNatural(caveats)}.`
      : `Many recent, closely matched transactions were available (${evidenceText}).`;
  }
  if (confidence === "MEDIUM") {
    return caveats.length > 0
      ? `A good number of relevant transactions were available (${evidenceText}), but ${joinNatural(caveats)}.`
      : `A good number of relevant transactions were available (${evidenceText}), with moderate matching strength.`;
  }
  if (confidence === "LOW") {
    return caveats.length > 0
      ? `Only limited or weakly matched transaction evidence was available, and ${joinNatural(caveats)}. Treat this estimate cautiously.`
      : `Only limited or weakly matched transaction evidence was available (${evidenceText}). Treat this estimate cautiously.`;
  }
  return "NearHome could not establish enough reliable transaction evidence for a confident estimate.";
}

function normalizeAddressPart(value: string | null | undefined): string {
  return (value ?? "").toUpperCase().replace(/\bST\b/g, "STREET").replace(/\s+/g, " ").trim();
}

function addressParts(address: string | null | undefined): { block: string; street: string } | null {
  if (!address) return null;
  const match = address.trim().match(/^(?:BLK\s*)?(\d+[A-Z]?)\s+(.+)$/i);
  return match ? { block: normalizeAddressPart(match[1]), street: normalizeAddressPart(match[2]) } : null;
}

function formatMonth(value: string | undefined): string | null {
  if (!value) return null;
  const match = value.match(/^(\d{4})-(\d{2})/);
  if (!match) return value;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("en-SG", { month: "short", year: "numeric", timeZone: "UTC" }).format(date);
}

function formatMonths(months: number): string {
  const years = Math.floor(months / 12);
  const remainder = months % 12;
  return remainder > 0 ? `About ${years} years ${remainder} months` : `About ${years} years`;
}

export function getHumanReadableEvidenceSource(source: string | null | undefined): string {
  switch ((source ?? "").toLowerCase()) {
    case "hdb_same_block_transactions":
    case "historical_transactions":
      return "same-block HDB transaction records";
    case "hdb_lease_commencement":
      return "HDB lease commencement records";
    case "listing_unverified":
      return "listing-provided information (not independently verified)";
    case "hdb_lease_estimator":
      return "HDB lease estimator";
    case "unavailable":
      return "not available";
    default:
      return source?.replace(/_/g, " ") || "not available";
  }
}

export function getComparableMatchLabel(similarity: number | null | undefined): string {
  const value = finiteNumber(similarity);
  if (value === null) return "Contextual match";
  if (value >= SIMILARITY_LABEL_BANDS.strong) return "Very similar";
  if (value >= SIMILARITY_LABEL_BANDS.good) return "Similar";
  if (value >= SIMILARITY_LABEL_BANDS.moderate) return "Moderately similar";
  return "Contextual match";
}

export function eligibleComparableCount(fairPrice: FairPriceData): number | null {
  const direct = finiteNumber(fairPrice.eligible_transaction_count);
  if (direct !== null) return Math.max(0, Math.round(direct));
  return evidenceNumber(fairPrice, "eligible_comparable_count");
}

export function comparableLeaseDisplay(comparable: FairPriceComparable): string {
  const months = positiveNumber(comparable.remaining_lease_months);
  if (months !== null) return formatMonths(Math.round(months));
  const years = positiveNumber(comparable.remaining_lease);
  return years !== null ? formatMonths(Math.round(years * 12)) : "Not available";
}

export function fairPriceModelDisplayName(fairPrice: FairPriceData): string | null {
  const method = (fairPrice.method ?? "").toUpperCase();
  if (method.includes("CATBOOST")) return "CatBoost";
  if (method.includes("WEIGHTED_COMPARABLE")) return "weighted-comparables";
  return null;
}

export function displayedComparables(fairPrice: FairPriceData): FairPriceComparable[] {
  const source = fairPrice.displayed_comparables ?? fairPrice.comparables ?? [];
  return [...source]
    .sort((left, right) => {
      const similarityDifference = (finiteNumber(right.similarity) ?? -1) - (finiteNumber(left.similarity) ?? -1);
      if (similarityDifference !== 0) return similarityDifference;
      const dateDifference = String(right.transaction_date ?? right.month ?? "").localeCompare(String(left.transaction_date ?? left.month ?? ""));
      if (dateDifference !== 0) return dateDifference;
      return String(left.transaction_id ?? "").localeCompare(String(right.transaction_id ?? ""));
    })
    .slice(0, 10);
}

export function buildComparableReason(
  comparable: FairPriceComparable,
  listing: SessionListing | undefined,
): string {
  const reasons: string[] = [];
  const targetAddress = addressParts(listing?.address);
  const comparableBlock = normalizeAddressPart(comparable.block);
  const comparableStreet = normalizeAddressPart(comparable.street);
  if (targetAddress && comparableBlock === targetAddress.block && comparableStreet === targetAddress.street) {
    reasons.push("Same street");
  } else if (targetAddress && comparableStreet === targetAddress.street) {
    reasons.push("Nearby block");
  }

  if (listing?.flat_type && comparable.flat_type && normalizeAddressPart(listing.flat_type) === normalizeAddressPart(comparable.flat_type)) {
    reasons.push("Same flat type");
  }
  if (listing?.flat_model && comparable.flat_model && normalizeAddressPart(listing.flat_model) === normalizeAddressPart(comparable.flat_model)) {
    reasons.push("Same flat model");
  }

  const targetArea = positiveNumber(listing?.floor_area_sqm);
  const comparableArea = positiveNumber(comparable.floor_area_sqm);
  if (targetArea !== null && comparableArea !== null) {
    const difference = Math.round(Math.abs(comparableArea - targetArea));
    reasons.push(difference <= 2 ? "Similar size" : `${difference} sqm ${comparableArea < targetArea ? "smaller" : "larger"}`);
  }

  const targetLease = positiveNumber(listing?.remaining_lease_months);
  const comparableLease = positiveNumber(comparable.remaining_lease_months);
  if (targetLease !== null && comparableLease !== null) {
    reasons.push(Math.abs(targetLease - comparableLease) <= 24 ? "Similar remaining lease" : "Comparable lease evidence available");
  }

  if (comparable.age_months !== null && comparable.age_months !== undefined && comparable.age_months <= 6) {
    reasons.push("Sold recently");
  }
  if (!listing?.storey_range || !comparable.storey_range) reasons.push("Storey comparison unavailable");
  return reasons.length > 0 ? reasons.join(", ") : "Relevant transaction in the selected comparison pool";
}

export function buildValuationLimitations(
  fairPrice: FairPriceData,
  listing: SessionListing | undefined,
): ValuationLimitation[] {
  const limitations: ValuationLimitation[] = [];
  const missing = missingFieldNames(fairPrice);
  if (missing.includes("storey information") || !listing?.storey_range) {
    limitations.push({
      title: "Missing storey information",
      description: "Storey can affect resale price, but NearHome could not adjust for whether this unit is on a low, middle or high floor.",
    });
  }
  if (missing.includes("flat model") || (!listing?.flat_model && !fairPrice.flat_model_used)) {
    limitations.push({
      title: "Flat model unavailable",
      description: "The exact HDB flat model was not confirmed, so model-specific similarity was not applied.",
    });
  }

  const lease = fairPrice.remaining_lease_estimate;
  const leaseConfidence = (lease?.confidence ?? fairPrice.remaining_lease_confidence ?? "").toLowerCase();
  const hasLeaseEvidence = Boolean(
    lease?.remaining_lease_months != null
      || lease?.display_value
      || lease?.source
      || fairPrice.remaining_lease_months_used != null
      || fairPrice.remaining_lease_source,
  );
  if (hasLeaseEvidence && (lease?.is_estimated || leaseConfidence !== "high")) {
    const source = getHumanReadableEvidenceSource(lease?.source ?? fairPrice.remaining_lease_source);
    limitations.push({
      title: "Remaining lease is estimated",
      description: `The lease value was derived from ${source}; month-level precision and source confidence should be considered.`,
    });
  } else if (!hasLeaseEvidence) {
    limitations.push({
      title: "Remaining lease unavailable",
      description: "NearHome could not find reliable lease evidence for this listing, so the estimate should be treated cautiously.",
    });
  }

  const spread = evidenceNumber(fairPrice, "comparable_price_spread");
  const central = positiveNumber(fairPrice.central_estimate);
  if ((spread !== null && central !== null && spread / central > 0.35) || fairPrice.confidence_reasons?.some((reason) => /price spread is wide/i.test(reason))) {
    limitations.push({
      title: "Wide variation in nearby sale prices",
      description: "Similar flats sold at noticeably different prices. Storey, renovation condition, facing, view and other details may explain differences that NearHome cannot currently observe.",
    });
  }

  const relaxationSteps = fairPrice.filter_status?.relaxation_steps;
  if (Array.isArray(relaxationSteps) && relaxationSteps.length > 0) {
    limitations.push({
      title: "Search criteria were widened",
      description: `NearHome widened the comparison after the initial criteria produced too little evidence: ${relaxationSteps.join(" ")}`,
    });
  }
  return limitations;
}

export function leaseDisplay(fairPrice: FairPriceData, listing: SessionListing | undefined): string {
  const display = fairPrice.remaining_lease_estimate?.display_value;
  if (display) return display.replace(/^Estimated remaining lease:\s*/i, "").replace(/^Listing states:\s*/i, "");
  const months = positiveNumber(fairPrice.remaining_lease_months_used ?? listing?.remaining_lease_months);
  return months !== null ? formatMonths(Math.round(months)) : "Unable to determine";
}

export function comparableDate(comparable: FairPriceComparable): string {
  return formatMonth(comparable.transaction_date ?? comparable.month) ?? "Date unavailable";
}
