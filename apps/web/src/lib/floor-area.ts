/** Manual-listing floor area is stored and sent to the API in square metres. */
export type FloorAreaUnit = "sqm" | "sqft";

export const SQ_FT_TO_SQ_M = 0.092903;

export function convertFloorArea(value: number, from: FloorAreaUnit, to: FloorAreaUnit): number {
  if (from === to) return value;
  const converted = from === "sqft" ? value * SQ_FT_TO_SQ_M : value / SQ_FT_TO_SQ_M;
  return Number(converted.toFixed(2));
}

export function canonicalFloorAreaSqm(value: number, unit: FloorAreaUnit): number {
  return unit === "sqft" ? convertFloorArea(value, "sqft", "sqm") : value;
}
