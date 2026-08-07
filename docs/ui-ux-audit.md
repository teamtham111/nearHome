# NearHome UI/UX audit

Date: 2026-08-05

## Current user journey

The main path is:

1. `/` creates or resumes a session.
2. `/session/[sessionId]` combines buyer profile, important location, school inputs,
   listing import/manual entry, shortlist management, and enrichment in one long workspace.
3. `/session/[sessionId]/comparison` shows requirements, recommendation, price, fair price,
   transport, driving, journeys, schools, and observations.

## Problems found before implementation

- The workspace had no compact progress indicator, so users had to infer whether they were
  configuring a profile, adding listings, or reviewing results.
- The profile, listing, and enrichment sections all used similarly prominent buttons, making the
  next action unclear on a laptop viewport.
- Requirements and preferences were not visually separated. The profile exposed priorities but
  did not explain that they rank listings rather than disqualify them.
- The comparison opened with a plain requirement list and a text-only recommendation. The leading
  home was not visually identifiable at a glance.
- Public transport, driving, and school scores used several different text treatments. A user had
  to scan multiple cards to understand whether a score was complete, provisional, or unavailable.
- Raw provider names, confidence labels, and implementation-oriented status values appeared in
  ordinary result text instead of being grouped as supporting evidence.
- Wide comparison tables could overflow on smaller laptop widths, and long sections made the
  page difficult to scan.

## Scope of the UI pass

This pass keeps the existing routes, APIs, data models, calculations, scoring, and provider
provenance unchanged. It adds presentation-only workflow guidance, reusable score indicators,
clearer grouping, and more consistent action styling. It does not invent new requirements or
change how any score is calculated.

## Homepage and workflow presentation

The public homepage retains the compact slate-and-teal presentation used by the deployed product.
It uses the existing start-comparison flow and three workflow cards; no navigation, routes, APIs,
data models, calculations, or comparison behaviour are changed.

The user-facing workflow has three stages: **Buyer profile**, **Add flats**, and **Compare
results**. A new session starts with the buyer profile; after the profile is saved, the listing
entry card becomes the next focused action. Enrichment is shown as a genuine intermediate
processing state, with weighted progress derived from the enrichment job and per-listing run
statuses. It is not an additional input stage. The workspace never renders enrichment progress or
comparison results inline: once at least two flats are confirmed, its single action opens the
dedicated comparison route and starts enrichment there. That route shows progress until the
durable job reaches a terminal state, then refreshes and renders the full comparison.

The buyer-profile UI contains ordinary comparison context—budget, transport mode, ranked
priorities, a regular destination, and schools. It deliberately contains no requirements or hard
filtering controls. The comparison UI likewise does not show requirement results or state that a
flat is included or excluded based on a pass/fail condition.

Smart Paste keeps copied listing text as the recommended route. URL input remains available
because it is connected, but accurately warns that listing websites can restrict automated
retrieval. The entry screen acknowledges a valid typed URL immediately, but only displays field
values after the user selects **Add a flat** and the real extraction completes; it never creates
a speculative link preview. Marketing previews are explicitly illustrative and isolated from the persisted
shortlist and API responses.

The homepage places a buyer-readable explanation directly after its three workflow cards. It
distinguishes discovery on a property portal from making a decision in NearHome, then presents
the four active assessments as responsive, equal-height evidence cards. Each card separates the
assessment purpose, the evidence it considers, and why that outcome matters to a buyer. The
following personalised-comparison section explains the concrete comparison outputs and links to
the existing evaluation route; it does not add routes, scoring, sample scores, or product claims.

## Preferences-step refinement

The buyer-profile card is now a centred, responsive Preferences step. Its buyer-facing choices
are ordered as main transport mode, decision priorities, then an optional regular destination;
the existing budget field and API payload are preserved. Main transport mode uses accessible
segmented buttons while retaining the existing `MAINLY_PUBLIC_TRANSPORT`, `MAINLY_DRIVING`, and
`BOTH` values. Priorities are a keyboard-operable ranked list of one to three unique factors.
Users add a factor through the app-styled picker, reorder it with a drag handle or keyboard, and
can remove it with the matching compact action; their saved order remains the order sent to the
recommendation engine.

Regular destination details are hidden until requested. A confirmed OneMap address can be
collapsed into a compact summary, reopened without data loss, or cleared through the existing
confirmation-dialog pattern. The destination editor retains its address validation, day, arrival
time, and travel-mode fields; its preferred-mode control uses the same segmented style. Clearing
the editor updates local form state, and the user saves the profile to persist that removal.

## Nearby-schools comparison refinement

The Nearby schools section is now a compact comparison rather than a per-listing calculation
dump. It shows the MOE-distance disclaimer once, derives a clear stronger listing only from the
available 1 km count, cumulative 2 km count, and materially closer nearest-school distance, and
uses a neutral similarity message otherwise. Listing cards present descriptive access labels and
the cumulative counts first; full school rows remain behind keyboard-accessible expand buttons.
Distances are formatted as rounded metres below 1 km or a maximum of two decimal places in km.
No-nearby-school and unavailable states use neutral language and colours rather than an error
treatment.

Selected-school matching now normalises casing, whitespace and punctuation, supports a small
set of documented unambiguous aliases, and can use an unambiguous common-suffix match. It keeps
the official MOE name after a match and leaves ambiguous choices unmatched. Selected-school
warnings are collected once at section level instead of repeating on every listing card.

## Comparison-result refinements

The quick comparison table now includes three evidence-backed rows after the core price, area,
and lease metrics: **Asking Price vs Estimated Value**, **Public Transport Strength**, and
**Driving Connectivity**. Each row reads the existing comparison response only. Fair-price cells
show the existing above/below/close assessment and confidence, while transport and driving cells
show the current score, rating, and whether the result is assessed or partial. Missing, failed,
or still-running enrichment is labelled as such rather than rendered as a numeric score.

The public-transport cards retain their overall score and buyer-facing evidence, but no longer
repeat component scores in a separate static summary. Their always-visible **Transport breakdown**
contains the four expandable component rows. Each full-width row is a keyboard-operable button
with `aria-expanded`, an accessible label, and a chevron that rotates when its own evidence is
shown. The cards also no longer show a separate trade-off, best-for, or technical-methodology
panel. The driving cards no longer expose the technical-assessment details disclosure. These are
presentation changes only; transport and driving calculations, weights, sources, and API calls
remain unchanged.

Driving Connectivity now follows the same breakdown pattern. Each listing keeps its general
neighbourhood score, status, headline, and short explanation above an always-visible **Driving
breakdown**. Major-road access, route flexibility, peak-hour access reliability, and parking
convenience are collapsed rows by default. Their expanded panels organise the existing evidence
under *What it means*, *Your result*, and *Supporting details*. A separate regular-destination
journey remains outside this general score. Driving also uses the same **Compared with your
shortlisted homes** table as Public Transport, covering the overall score and each driving
component with the same underlying-score tie treatment.

The decision overview deliberately does not display an Overall fit number. It keeps the green
recommended-listing treatment and the recommendation rationale. The backend still calculates
`overall_fit_score` as an absolute weighted value for deterministic ranking; rank remains
separate metadata, and unassessed preferences are excluded from the weighted denominator.

## Shared workflow design system

The audit found one visible grid regression: the Preferences card added a local `max-w-5xl`
constraint while the Add a flat card used the workspace width. The shared `nh-page-grid` and
`nh-workflow-grid` primitives now define one responsive 6xl content width and gutters for the
header, pages, stepper, and workflow cards. Major cards use the shared `nh-card` shell, so their
edges, padding, borders, radius, and mobile spacing align without matching their content-driven
heights.

Reusable control primitives now cover primary, secondary and destructive actions, compact
icon-only actions, fields, selects, textareas, labels, helper text, and tabs. They provide
consistent 44px targets, focus rings, selected states, disabled states, border treatment, and
number-input styling. These are presentation-only classes: API contracts, validation, enrichment,
and all recommendation logic are unchanged.

The transport and driving comparison panels use the medium score-ring size so the score and its
`/100` context remain comfortably legible without changing any score calculation or rating.

The Buyer Profile now presents budget as a compact SGD field group, priority ordering with
accessible move/remove icons, and clearly labelled optional destination and school controls.
Its primary action reads **Save profile and continue** before an initial save. Add a flat now
uses the same card and form rhythm, semantic manual/Smart Paste tabs, field helpers that clarify
flat type versus subtype versus HDB model, and an explicit explanation when the required manual
fields are incomplete. The progress stepper only links to safe previous steps; active and future
steps remain informative but are not misleading navigation links.

The budget amount remains a numeric form value with no in-input currency decoration. The field
label and helper text identify Singapore dollars, leaving the value fully unobstructed and
ensuring no formatting can become part of the submitted value.
