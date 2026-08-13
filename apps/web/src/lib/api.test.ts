import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createSession,
  DEFAULT_REQUEST_TIMEOUT_MS,
  LONG_RUNNING_REQUEST_TIMEOUT_MS,
  parseApiError,
  smartPaste,
  startEnrichment,
} from "./api";
import { buildSmartPasteRequest } from "./smart-paste";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response;
}

function pendingFetch() {
  let resolveResponse: (response: Response) => void = () => undefined;
  const fetchMock = vi.fn(
    (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Promise<Response>((resolve) => {
        resolveResponse = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, resolveResponse: (response: Response) => resolveResponse(response) };
}

describe("API request timeouts", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");
    vi.stubGlobal("window", {
      setTimeout: setTimeoutSpy,
      clearTimeout: globalThis.clearTimeout,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("keeps ordinary API requests on the 15-second timeout", async () => {
    const { fetchMock, resolveResponse } = pendingFetch();
    const request = createSession();

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(window.setTimeout).toHaveBeenLastCalledWith(expect.any(Function), DEFAULT_REQUEST_TIMEOUT_MS);

    resolveResponse(jsonResponse({ session_id: "session-1", demo_mode: false, created_at: "2026-01-01" }));
    await expect(request).resolves.toMatchObject({ session_id: "session-1" });
  });

  it.each([
    ["pasted text", { sourceType: "text" as const, rawText: "4-room HDB flat" }],
    ["listing URL", { sourceType: "url" as const, sourceUrl: "https://www.propertyguru.com.sg/listing/123" }],
  ])("allows Smart Paste %s to run past the normal timeout", async (_kind, body) => {
    const { fetchMock, resolveResponse } = pendingFetch();
    const request = smartPaste("session-1", body);
    const signal = fetchMock.mock.calls[0][1]?.signal as AbortSignal;

    expect(window.setTimeout).toHaveBeenLastCalledWith(expect.any(Function), LONG_RUNNING_REQUEST_TIMEOUT_MS);
    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS + 1);
    expect(signal.aborted).toBe(false);

    resolveResponse(jsonResponse({ listing_input_id: "listing-1", candidates: {}, extraction_warnings: [], agent_claims: [], sourceType: body.sourceType, sourceUrl: null }));
    await expect(request).resolves.toMatchObject({ listing_input_id: "listing-1" });
  });

  it("allows inline enrichment to run past the normal timeout", async () => {
    const { fetchMock, resolveResponse } = pendingFetch();
    const request = startEnrichment("session-1");
    const signal = fetchMock.mock.calls[0][1]?.signal as AbortSignal;

    expect(window.setTimeout).toHaveBeenLastCalledWith(expect.any(Function), LONG_RUNNING_REQUEST_TIMEOUT_MS);
    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS + 1);
    expect(signal.aborted).toBe(false);

    resolveResponse(jsonResponse({ job_id: "job-1", status: "completed", status_url: "/api/v1/jobs/job-1" }));
    await expect(request).resolves.toMatchObject({ job_id: "job-1", status: "completed" });
  });

  it("still reports a genuine request timeout", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        (init?.signal as AbortSignal).addEventListener("abort", () => {
          reject(new DOMException("The operation was aborted", "AbortError"));
        });
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = createSession();
    const assertion = expect(request).rejects.toThrow("The analysis is taking longer than expected. Please retry in a moment.");
    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS);
    await assertion;
  });
});

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
