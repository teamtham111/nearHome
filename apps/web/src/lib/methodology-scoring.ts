// Mirrors apps/api/app/engines/preference_scoring.py (`_absolute_score`, weights, tie rule) so the
// methodology page can compute its sample figures instead of typing them by hand. Keep in step with
// the Python code; methodology-scoring.test.ts pins the sample numbers shown on the page.

export const PRIORITY_WEIGHTS_BY_RANK = [0.45, 0.35, 0.2] as const; // apps/api/app/domain/models.py
export const TIE_THRESHOLD = 3; // overall-fit points

export const FAIR_PRICE_CONFIDENCE_FACTOR = { HIGH: 1, MEDIUM: 0.8, LOW: 0.5, OTHER: 0.25 } as const;

const clamp = (score: number) => Math.max(0, Math.min(100, score));
export const round1 = (value: number) => Math.round(value * 10) / 10;
export const round2 = (value: number) => Math.round(value * 100) / 100;

export function affordabilityScore(askingPrice: number, maxBudget: number) {
  const ratio = maxBudget > 0 ? askingPrice / maxBudget : Number.POSITIVE_INFINITY;
  return clamp(100 - Math.max(0, ratio - 0.75) * 200);
}

export function spaceScore(floorAreaSqm: number) {
  return clamp(((floorAreaSqm - 60) / 60) * 100);
}

export function leaseScore(remainingYears: number) {
  return clamp(((remainingYears - 40) / 40) * 100);
}

export function journeyScore(minutes: number) {
  return clamp(100 - ((minutes - 15) / 60) * 100);
}

export function fairPriceScore(askingPrice: number, estimate: number, factor: number) {
  const gap = (estimate - askingPrice) / estimate;
  return clamp(50 + gap * factor * 500);
}

/** Weighted average of the assessed parts; weights are rescaled over the parts that have a score. */
export function weightedAverage(parts: Array<{ score: number | null; weight: number }>) {
  const assessed = parts.filter((part): part is { score: number; weight: number } => part.score !== null);
  const total = assessed.reduce((sum, part) => sum + part.weight, 0);
  if (!total) return null;
  return assessed.reduce((sum, part) => sum + part.score * part.weight, 0) / total;
}

/** Priority weights after dropping any priority without a score and rescaling the rest. */
export function rescaledWeights(rankedHasScore: boolean[]) {
  const raw: number[] = rankedHasScore.map((has, index) => (has ? PRIORITY_WEIGHTS_BY_RANK[index] : 0));
  const total = raw.reduce((sum, weight) => sum + weight, 0);
  return raw.map((weight) => (total ? weight / total : 0));
}

export function isTie(overallFits: number[]) {
  if (overallFits.length < 2) return false;
  const max = Math.max(...overallFits);
  return max - Math.min(...overallFits) <= TIE_THRESHOLD && max > 0;
}
