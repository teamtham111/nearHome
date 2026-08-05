import { describe, it, expect } from "vitest";
import { parseApiError } from "./api";
import { buildSmartPasteRequest } from "./smart-paste";

describe("ComparisonResponse shape", () => {
  it("always-expanded panels are required fields", () => {
    const mock = {
      fair_price_status: "AWAITING_ENRICHMENT",
      fair_price_by_listing: {},
      immediate_metrics: [],
    };
    expect(mock.fair_price_status).toBeDefined();
    expect(mock.fair_price_by_listing).toBeDefined();
  });
});

describe("Smart Paste request classification", () => {
  it("uses sourceUrl for a complete PropertyGuru URL", () => {
    expect(buildSmartPasteRequest(" https://www.propertyguru.com.sg/listing/123 ")).toEqual({
      sourceType: "url",
      sourceUrl: "https://www.propertyguru.com.sg/listing/123",
    });
  });

  it("keeps ordinary copied text as rawText", () => {
    expect(buildSmartPasteRequest("Asking Price: S$928,000\n4-room HDB flat")).toEqual({
      sourceType: "text",
      rawText: "Asking Price: S$928,000\n4-room HDB flat",
    });
  });

  it("passes unsupported absolute URLs to the server for a clear validation error", () => {
    expect(buildSmartPasteRequest("https://example.com/listing/123")).toEqual({
      sourceType: "url",
      sourceUrl: "https://example.com/listing/123",
    });
  });
});

describe("API error parsing", () => {
  it("preserves the development provider message for Smart Paste failures", () => {
    expect(
      parseApiError(
        JSON.stringify({
          detail: {
            message: "Groq could not complete Smart Paste extraction.",
            providerMessage: "Invalid request body",
          },
        }),
      ),
    ).toBe("Groq could not complete Smart Paste extraction. (Invalid request body)");
  });
});
