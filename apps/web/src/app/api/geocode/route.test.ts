import { beforeEach, describe, expect, it, vi } from "vitest";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function postGeocode(body: unknown) {
  const { POST } = await import("./route");
  return POST(
    new Request("http://localhost/api/geocode", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

describe("POST /api/geocode", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.restoreAllMocks();
    process.env.ONEMAP_EMAIL = "test@example.com";
    process.env.ONEMAP_PASSWORD = "test-password";
  });

  it("rejects an invalid query without calling OneMap", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await postGeocode({ query: "A" });

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({
      code: "INVALID_REQUEST",
      message: "Enter at least two characters",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("authenticates with OneMap and normalizes string coordinates", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ access_token: "token" }))
      .mockResolvedValueOnce(
        jsonResponse({
          results: [
            {
              SEARCHVAL: "ORCHARD ROAD",
              ADDRESS: "1 ORCHARD ROAD SINGAPORE 238823",
              POSTAL: "238823",
              LATITUDE: "1.3048",
              LONGITUDE: "103.8318",
            },
            { SEARCHVAL: "invalid result", LATITUDE: "not-a-number" },
          ],
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const response = await postGeocode({ query: "Orchard Road" });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      suggestions: [
        {
          place_id: "onemap:1.3048:103.8318:238823",
          description: "1 ORCHARD ROAD SINGAPORE 238823",
          main_text: "ORCHARD ROAD",
          formatted_address: "1 ORCHARD ROAD SINGAPORE 238823",
          latitude: 1.3048,
          longitude: 103.8318,
        },
      ],
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("https://www.onemap.gov.sg/api/auth/post/getToken");
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("searchVal=Orchard+Road");
  });

  it("returns a structured provider error instead of leaking an upstream failure", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse({ error: "forbidden" }, 403));
    vi.stubGlobal("fetch", fetchMock);

    const response = await postGeocode({ query: "Orchard Road" });

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({
      code: "GEOCODING_UNAVAILABLE",
      message: "The Singapore address service could not complete authentication (403).",
    });
  });
});
