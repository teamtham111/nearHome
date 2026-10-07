import { describe, expect, it } from "vitest";
import {
  FAIR_PRICE_CONFIDENCE_FACTOR,
  affordabilityScore,
  fairPriceScore,
  isTie,
  journeyScore,
  leaseScore,
  rescaledWeights,
  round1,
  round2,
  spaceScore,
  weightedAverage,
} from "./methodology-scoring";

describe("methodology scoring mirror", () => {
  it("scores affordability from the share of budget", () => {
    expect(affordabilityScore(675000, 900000)).toBe(100);
    expect(round2(affordabilityScore(838000, 900000))).toBe(63.78);
    expect(affordabilityScore(1125000, 900000)).toBe(0);
  });

  it("maps space, lease and journey time onto 0–100", () => {
    expect(spaceScore(60)).toBe(0);
    expect(spaceScore(120)).toBe(100);
    expect(round2(spaceScore(112))).toBe(86.67);
    expect(leaseScore(40)).toBe(0);
    expect(leaseScore(80)).toBe(100);
    expect(leaseScore(63)).toBeCloseTo(57.5, 6);
    expect(journeyScore(15)).toBe(100);
    expect(journeyScore(75)).toBe(0);
    expect(journeyScore(45)).toBe(50);
  });

  it("turns the fair-price gap into points using the confidence factor", () => {
    expect(fairPriceScore(832600, 832600, 1)).toBe(50);
    expect(round2(fairPriceScore(838000, 832600, FAIR_PRICE_CONFIDENCE_FACTOR.HIGH))).toBe(46.76);
    // 1% below the estimate adds 5 points at high confidence and 4 at medium.
    expect(fairPriceScore(990, 1000, 1)).toBeCloseTo(55, 6);
    expect(fairPriceScore(990, 1000, 0.8)).toBeCloseTo(54, 6);
  });

  it("builds the sample category totals shown on the page", () => {
    const transport = weightedAverage([
      { score: 80, weight: 30 }, { score: 82.9, weight: 25 }, { score: 73.3, weight: 30 }, { score: 84, weight: 15 },
    ]);
    expect(round1(transport ?? 0)).toBe(79.3);
    const driving = weightedAverage([
      { score: 85, weight: 30 }, { score: 89, weight: 25 }, { score: 85, weight: 25 }, { score: 72.6, weight: 20 },
    ]);
    expect(round1(driving ?? 0)).toBe(83.5);
  });

  it("rescales priority weights when a priority has no score", () => {
    expect(rescaledWeights([true, true, true])).toEqual([0.45, 0.35, 0.2]);
    const [first, second, third] = rescaledWeights([true, true, false]);
    expect(first).toBeCloseTo(0.5625, 6);
    expect(second).toBeCloseTo(0.4375, 6);
    expect(third).toBe(0);
  });

  it("computes the sample overall fit and the tie rule", () => {
    const fit = weightedAverage([
      { score: round2(affordabilityScore(838000, 900000)), weight: 45 },
      { score: round2(spaceScore(112)), weight: 35 },
      { score: 79.3, weight: 20 },
    ]);
    expect(round1(fit ?? 0)).toBe(74.9);
    expect(isTie([74.9, 72.4])).toBe(true);
    expect(isTie([74.9, 70.8])).toBe(false);
  });
});
