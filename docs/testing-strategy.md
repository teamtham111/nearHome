# NearHome Testing Strategy

## Pyramid

```text
        E2E (Playwright)           — critical user journeys
       /                    \
  Integration (pytest + PG)    — repos, adapters with fixtures
 /                              \
Unit (pytest + Vitest)           — engines, validators, UI components
```

## Backend unit tests (`apps/api/app/tests/`)

Storey range is an optional confirmation field and is never extracted by Smart Paste.
Historical transaction fixtures retain `storey_range` as source evidence for the
optional similarity component.

| Area | Cases |
| --- | --- |
| Immediate comparison | signed budget difference, PPSM, canonical month-based lease, missing lease, retired-metric absence |
| Requirement engine | legacy journey metric rejection, maximum driving journey PASS/FAIL/CANNOT_DETERMINE |
| Preference scoring | lower-is-better, higher-is-better, ties |
| Recommendation | failing listing excluded when pass exists; asking-price affordability |
| Profile schema | one-to-three ordered priorities, duplicate rejection, up to 10 normalized named schools |
| Smart Paste attributes | PropertyGuru/99.co-style flat labels, canonical subtype/model mapping, ambiguity/conflict review, no storey prefill |
| Fair-price filter state | town derivation, missing optional fields, exact relaxation messages |
| Fair-price UI translation | asking-price direction and threshold boundaries, confidence prose, missing/relaxed evidence, capped top-10 contextual comparables, eligible-count wording, similarity labels and hidden diagnostics |
| Shortlist removal | stable-ID deletion, confirmation/cancel, persistence rollback, cascading listing state, combined Add a flat entry area and one/zero-listing behaviour |
| Public Transport presentation | rating boundaries, practical interpretation, honest missing data, tie-aware shortlist positions and progressive disclosure |
| Driving presentation | four-component weights, exact 76-point rollup formula, no-destination completeness, separate destination journey presentation, route singular/plural wording, parking-field omission, availability freshness and Singapore-time formatting |

Run: `cd apps/api && pytest`

## Integration tests (`tests/integration/` — Phase 2)

- PostgreSQL repositories with test database
- Smart Paste with recorded LLM fixtures
- Adapter mocks with partial route-matrix failure

CI does **not** depend on live paid APIs.

## Frontend tests (`apps/web/`)

Vitest + React Testing Library for:

- Priority max 3 validation
- Always-expanded price/fair-price panels
- Unavailable journey states (Phase 2)

Run: `cd apps/web && npm test`

## E2E (`tests/e2e/` — Phase 5)

Playwright flows from master prompt §31:

1. Multiple named-school profile save and reload
2. Fair-price evidence display without raw model diagnostics
3. Shortlist removal, confirmation, failure rollback and one/zero-listing states
4. Manual two-listing comparison (no external API)
5. Smart Paste confirmation
6. Three-listing production fair-price enrichment, including comparable evidence, queued-run polling and stale-run protection
7. Budget requirement pass/fail
8. Practical tie
9. Mobile comparison
10. Priority custom factor picker and drag-and-drop/keyboard reordering, with ranks updating after a drop
11. Buyer-profile-first workflow, with flat entry becoming available after profile save
12. Dedicated comparison route starts enrichment from the shortlist action, shows progress, then displays refreshed results

## CI gates

- `ruff check`
- `mypy app`
- `pytest`
- `npm run typecheck && npm run build`

## Coverage targets

- Engines: >90% line coverage
- Critical paths (requirements, recommendation): 100% branch coverage on status enums
