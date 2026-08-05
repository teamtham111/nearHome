export type SmartPasteRequest =
  | { sourceType: "url"; sourceUrl: string }
  | { sourceType: "text"; rawText: string };

export function buildSmartPasteRequest(input: string): SmartPasteRequest {
  const trimmed = input.trim();
  try {
    const url = new URL(trimmed);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return { sourceType: "url", sourceUrl: trimmed };
    }
  } catch {
    // Ordinary copied listing text is expected to be non-URL input.
  }
  return { sourceType: "text", rawText: input };
}
