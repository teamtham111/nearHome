import { describe, expect, it } from "vitest";
import { getSmartPasteProgress } from "./smart-paste-progress";

describe("getSmartPasteProgress", () => {
  it("starts URL extraction at retrieving stage", () => {
    const result = getSmartPasteProgress(0, "url", true);
    expect(result.label).toBe("Retrieving listing page…");
    expect(result.percent).toBe(0);
  });

  it("advances URL extraction through prepare and extract stages", () => {
    const retrieving = getSmartPasteProgress(7_000, "url", true);
    expect(retrieving.label).toBe("Retrieving listing page…");
    expect(retrieving.percent).toBeGreaterThan(10);
    expect(retrieving.percent).toBeLessThan(38);

    const preparing = getSmartPasteProgress(15_000, "url", true);
    expect(preparing.label).toBe("Preparing content…");
    expect(preparing.percent).toBeGreaterThan(38);

    const extracting = getSmartPasteProgress(25_000, "url", true);
    expect(extracting.label).toBe("Extracting property information…");
    expect(extracting.percent).toBeGreaterThan(48);
  });

  it("caps in-flight progress below 100%", () => {
    const result = getSmartPasteProgress(120_000, "url", true);
    expect(result.percent).toBeLessThanOrEqual(94);
  });

  it("keeps creeping during long validation waits", () => {
    const result = getSmartPasteProgress(120_000, "url", true);
    expect(result.label).toBe("Validating extracted fields…");
    expect(result.percent).toBeGreaterThan(90);
    expect(result.percent).toBeLessThanOrEqual(94);
  });

  it("uses shorter text-only stages", () => {
    const early = getSmartPasteProgress(800, "text", true);
    expect(early.label).toBe("Preparing content…");

    const extracting = getSmartPasteProgress(5_000, "text", true);
    expect(extracting.label).toBe("Extracting property information…");
    expect(extracting.percent).toBeGreaterThan(18);
  });
});
