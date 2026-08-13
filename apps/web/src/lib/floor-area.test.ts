import { describe, expect, it } from "vitest";

import { canonicalFloorAreaSqm, convertFloorArea } from "./floor-area";

describe("manual floor-area conversion", () => {
  it("keeps square metres as the canonical stored unit", () => {
    expect(canonicalFloorAreaSqm(91, "sqm")).toBe(91);
  });

  it("converts square feet to the equivalent canonical square metres", () => {
    expect(canonicalFloorAreaSqm(1108, "sqft")).toBe(102.94);
  });

  it("converts a displayed value between units without accumulating hidden precision", () => {
    expect(convertFloorArea(91, "sqm", "sqft")).toBe(979.52);
    expect(convertFloorArea(979.52, "sqft", "sqm")).toBe(91);
  });
});
