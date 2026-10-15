# Changelog

Project changes are recorded newest first. Each entry uses the local timestamp
and briefly describes the files or behavior changed.

## 2026-10-16 02:05:00 +08:00 (Asia/Singapore)

- Added block-level location features: listings are linked to a stable HDB
  block (registry from HDB Property Information, all 10,817 residential blocks
  geocoded with OneMap) and Public Transport / driving results are calculated
  once per block and reused across units.
- Added a OneMap routing provider. Public Transport and baseline major-road
  access (now a baseline, not AM-peak, driving time) use OneMap; Google is
  kept only for route flexibility and estimated peak-hour delay on
  shortlisted homes.
- Added versioned Singapore/town "Better than X% of HDB blocks" comparisons
  for six approved measurements, a free backfill job that cannot call Google,
  and a `LOCATION_BENCHMARK_DISPLAY` switch (default `shadow`). Parking,
  route flexibility and peak-hour delay are evidence only. See
  docs/location-benchmarks.md.

## 2026-10-05 17:25:50 +08:00 (Asia/Singapore)

- Rebuilt the Docker web image from the current source so the interactive demo
  and demo listing routes are included. Resolved the React production-build
  lint errors in the demo state transitions. Verified the homepage, demo
  listing, demo walkthrough, saved session, and API readiness routes return
  HTTP 200.

## 2026-10-05 17:03:16 +08:00 (Asia/Singapore)

- Recovered the local Docker environment after the Mac data volume became full
  and Docker's Linux disk reported write failures. Cleared the npm download
  cache, restarted Docker Desktop, stopped a stale non-Docker Next.js process
  holding port 3000, and started the full Compose stack successfully.

## 2026-10-05 03:23:12 +08:00 (Asia/Singapore)

- Changed the configured Smart Paste Gemini model to `gemini-3.5-flash-lite`
  after the previous model returned temporary high-demand `503` responses.
  Recreated the Docker API and worker and confirmed a live schema-valid
  extraction of synthetic property data.

## 2026-10-05 02:50:17 +08:00 (Asia/Singapore)

- Fixed Gemini Smart Paste structured-output validation by routing Gemini through
  Google's native JSON Schema API and normalizing the schema for its supported
  subset. Kept Groq and other OpenAI-compatible provider paths intact, added
  safe schema-validation diagnostics, and updated setup guidance.
- Rebuilt the Docker API and worker; readiness is healthy. The end-to-end smoke
  call reached Gemini but could not complete because Google's API returned a
  temporary `503 UNAVAILABLE` high-demand response.

## 2026-10-05 02:36:12 +08:00 (Asia/Singapore)

- Enabled the configured Gemini provider in the local Docker API and worker by
  rebuilding their images. Verified a live `gemini-3.8-flash` chat completion
  returned `OK`; the Gemini key value is intentionally not recorded.

## 2026-10-05 00:51:11 +08:00 (Asia/Singapore)

- Made the homepage evaluation-category selector interactive for Public
  transport, Driving, Fair price, and Schools. Each selection now updates the
  self-check steps, NearHome summary, comparison prompts, and category-specific
  caveat. Rebuilt the Docker web container successfully.

## 2026-10-05 00:43:59 +08:00 (Asia/Singapore)

- Refreshed the NearHome frontend with a field-atlas visual system: bundled
  Instrument Serif, Source Sans 3, and IBM Plex Mono fonts; warm paper and grid
  backgrounds; pine and vermilion colors; updated shared cards, forms, and
  navigation styling.
- Reworked the homepage hero into an editorial introduction beside the product
  comparison preview, with responsive layout and entrance motion.
- Rebuilt the Docker web container; homepage and font asset returned HTTP 200,
  and API readiness returned HTTP 200.
