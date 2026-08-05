import { NextResponse } from "next/server";
import { z } from "zod";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const requestSchema = z.object({
  query: z.string().trim().min(2, "Enter at least two characters").max(200, "Address is too long"),
});

const ONEMAP_AUTH_URL = "https://www.onemap.gov.sg/api/auth/post/getToken";
const ONEMAP_SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search";
const REQUEST_TIMEOUT_MS = 10_000;
const TOKEN_TTL_MS = 5 * 60 * 1000;

type OneMapResult = {
  SEARCHVAL?: unknown;
  ADDRESS?: unknown;
  POSTAL?: unknown;
  LATITUDE?: unknown;
  LONGITUDE?: unknown;
};

type OneMapTokenResponse = {
  access_token?: unknown;
};

type OneMapSearchResponse = {
  results?: unknown;
};

type CachedToken = {
  value: string;
  expiresAt: number;
};

let cachedToken: CachedToken | null = null;

class GeocodeProviderError extends Error {
  constructor(
    message: string,
    readonly code = "GEOCODING_UNAVAILABLE",
    readonly status = 502,
  ) {
    super(message);
  }
}

function jsonResponse(body: Record<string, unknown>, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

async function readJson<T>(response: Response, providerStep: string): Promise<T> {
  const contentType = response.headers.get("content-type") ?? "";
  const rawBody = await response.text();

  if (!response.ok) {
    throw new GeocodeProviderError(
      `The Singapore address service could not complete ${providerStep} (${response.status}).`,
    );
  }

  if (!rawBody || !contentType.toLowerCase().includes("json")) {
    throw new GeocodeProviderError(
      `The Singapore address service returned an invalid response during ${providerStep}.`,
    );
  }

  try {
    return JSON.parse(rawBody) as T;
  } catch {
    throw new GeocodeProviderError(
      `The Singapore address service returned malformed data during ${providerStep}.`,
    );
  }
}

function requiredCredential(name: "ONEMAP_EMAIL" | "ONEMAP_PASSWORD") {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new GeocodeProviderError(
      "Live Singapore address search is not configured on the server. Add the OneMap credentials and retry.",
      "GEOCODING_NOT_CONFIGURED",
      503,
    );
  }
  return value;
}

async function getOneMapToken(forceRefresh = false): Promise<string> {
  if (!forceRefresh && cachedToken && cachedToken.expiresAt > Date.now()) {
    return cachedToken.value;
  }

  const email = requiredCredential("ONEMAP_EMAIL");
  const password = requiredCredential("ONEMAP_PASSWORD");
  let response: Response;
  try {
    response = await fetch(ONEMAP_AUTH_URL, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new GeocodeProviderError(
      "The Singapore address service could not be reached. Check your connection and retry.",
    );
  }

  const data = await readJson<OneMapTokenResponse>(response, "authentication");
  const token = typeof data.access_token === "string" ? data.access_token.trim() : "";
  if (!token) {
    throw new GeocodeProviderError("The Singapore address service did not return an access token.");
  }

  cachedToken = { value: token, expiresAt: Date.now() + TOKEN_TTL_MS };
  return token;
}

function asText(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function asCoordinate(value: unknown) {
  const coordinate = typeof value === "number" ? value : Number(value);
  return Number.isFinite(coordinate) ? coordinate : null;
}

function normalizeSuggestion(result: OneMapResult, index: number) {
  const latitude = asCoordinate(result.LATITUDE);
  const longitude = asCoordinate(result.LONGITUDE);
  const formattedAddress = asText(result.ADDRESS) || asText(result.SEARCHVAL);
  const mainText = asText(result.SEARCHVAL) || formattedAddress;

  if (latitude === null || longitude === null || !formattedAddress) return null;

  const postal = asText(result.POSTAL);
  return {
    place_id: `onemap:${latitude}:${longitude}:${postal || index}`,
    description: formattedAddress,
    main_text: mainText,
    formatted_address: formattedAddress,
    latitude,
    longitude,
  };
}

async function searchOneMap(query: string) {
  let token = await getOneMapToken();
  const url = new URL(ONEMAP_SEARCH_URL);
  url.searchParams.set("searchVal", query);
  url.searchParams.set("returnGeom", "Y");
  url.searchParams.set("getAddrDetails", "Y");
  url.searchParams.set("pageNum", "1");

  let response: Response;
  try {
    response = await fetch(url, {
      headers: { Accept: "application/json", Authorization: token },
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new GeocodeProviderError(
      "The Singapore address service could not be reached. Check your connection and retry.",
    );
  }

  // OneMap tokens can expire before the local cache. Refresh once on an auth failure.
  if (response.status === 401 || response.status === 403) {
    cachedToken = null;
    token = await getOneMapToken(true);
    try {
      response = await fetch(url, {
        headers: { Accept: "application/json", Authorization: token },
        cache: "no-store",
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
    } catch {
      throw new GeocodeProviderError(
        "The Singapore address service could not be reached. Check your connection and retry.",
      );
    }
  }

  const data = await readJson<OneMapSearchResponse>(response, "address search");
  const results = Array.isArray(data.results) ? data.results : [];
  const suggestions = results
    .slice(0, 8)
    .map((result, index) => normalizeSuggestion(result as OneMapResult, index))
    .filter((result): result is NonNullable<typeof result> => result !== null);

  return { suggestions };
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonResponse(
      {
        code: "INVALID_REQUEST",
        message: "Enter a Singapore location to search.",
      },
      400,
    );
  }

  const parsed = requestSchema.safeParse(body);
  if (!parsed.success) {
    return jsonResponse(
      {
        code: "INVALID_REQUEST",
        message: parsed.error.issues[0]?.message ?? "Enter a Singapore location to search.",
      },
      400,
    );
  }

  try {
    return jsonResponse(await searchOneMap(parsed.data.query), 200);
  } catch (error) {
    if (error instanceof GeocodeProviderError) {
      return jsonResponse({ code: error.code, message: error.message }, error.status);
    }
    return jsonResponse(
      {
        code: "GEOCODING_UNAVAILABLE",
        message: "Singapore address search failed unexpectedly. Please retry.",
      },
      502,
    );
  }
}

