# Fair-price audit and implementation record

Updated 2026-08-05.

The buyer-facing translation layer is documented separately in
[fair-price-ui.md](./fair-price-ui.md). It intentionally hides raw model and
filter diagnostics while retaining them in the API for auditability.

## Current production path

Fair-price valuation is implemented by `apps/api/app/engines/fair_price.py` and
`fair_price_comparables.py`. The API loads HDB transaction records, excludes
transactions at or after the valuation month, progressively relaxes comparable
filters when necessary, and records the relaxation explicitly.

Equivalent source labels such as `5 ROOM HDB` and `5 ROOM (5I)` are normalized to
the HDB transaction label `5 ROOM` before comparable selection.

The production calculation uses CatBoost as the primary estimator and weighted
comparables as supporting evidence plus an explicit fallback:

```text
CatBoost features   = area, storey, lease, transaction month, town, type, model
central estimate    = CatBoost prediction trained before valuation month
range              = temporally calibrated CatBoost residual interval
fallback            = weighted comparable estimate and quantile range
```

The valuation engine computes weights and estimates from the complete unique
eligible collection. It retains all weighted rows in backend enrichment/audit
data, while the normal comparison response exposes only `eligible_transaction_count`
and at most ten `displayed_comparables`, ordered by canonical similarity,
recency and transaction ID. The capped response is a presentation boundary and
does not restrict CatBoost inputs or the weighted-comparable estimate. The API
also retains candidate counts, effective weighted count, similarity, transaction
age, spread and warnings.
Missing facts are reported; they are not fabricated.

Lease is canonicalized internally as integer months. Resolution follows this order:
official exact remaining lease; recent valid transactions from the exact normalized
block/street (median estimated expiry month); HDB lease commencement year; and finally
listing-stated lease as low-confidence, unverified evidence. Same-block expiry
disagreement of 0–6 months is high confidence, 7–24 months is medium, and more than
24 months is low with a warning. Commencement-year fallback displays approximate
whole years; transaction-derived evidence may display months. The estimate, source
records and as-of date are stored with the valuation.

The resolved canonical lease months, compatible years display value, source,
confidence and as-of date are also persisted onto the confirmed listing during
enrichment. This keeps the listing API, immediate comparison, fair-price model
and UI on the same date-aware lease value.

The confirmed-listing path keeps source evidence separate from canonical matching fields:

- `flat_type`: canonical room category used for matching;
- `raw_listing_subtype`: original compact source code such as `4A` or `5STD`;
- `listing_flat_subtype`: backward-compatible alias for the raw source code;
- `flat_model`: canonical explicit or deterministically subtype-derived HDB model;
- `storey_range`: optional user input only, never Smart Paste output;
- `town`: authoritative geocoder/confirmed value first, then derived from an
  exact historical transaction match when the authoritative value is absent.

The fair-price comparable engine uses only canonical `flat_type` and `flat_model`; it
does not score `raw_listing_subtype` separately, avoiding double-counting the same
property characteristic. Town derivation and lease matching share
`apps/api/app/utils/hdb_address.py`. The canonical key uppercases text,
removes embedded postal codes and punctuation, removes apostrophes, collapses
whitespace, preserves the HDB block number and expands common Singapore street
suffixes (`RD`/`ROAD`, `ST`/`STREET`, `AVE`/`AVENUE`, `DR`/`DRIVE`,
`CRES`/`CRESCENT`, `CL`/`CLOSE`, and related mappings). Matching is exact on
the resulting `(block, street)` pair; it is never a substring match. If one
canonical key maps to multiple towns, town remains unresolved and the
diagnostic evidence records the ambiguity.

Comparable explanations are generated from one persisted filter-status object. This
prevents a missing-town warning from being combined with a claim that same-town
matching was applied. The response also records whether town, flat type, model,
area, lease and storey criteria were applied, omitted or relaxed.

## Model selection decision

The recorded chronological benchmark compared weighted comparables with several
structured prediction candidates. CatBoost achieved final-test MAE S$36,041.11,
16.5% lower than weighted comparables at S$43,170.31, with 100% coverage. The
reviewer therefore recommended replacing the baseline with CatBoost, and that
recommendation is now applied by `fair_price_catboost.py`.

## Verification

- Backend unit and integration tests cover month-based lease resolution, date cutoffs, lease usage, asking-price
  invariance, progressive selection, insufficient evidence and preference scoring.
- The fair-price adapter test confirms an available result uses
  `CATBOOST`.
- The front end displays the range, confidence, buyer-facing price assessment,
  translated filter summary and comparable evidence. It does not display the
  method/version identifiers or raw filter diagnostics; see
  [fair-price-ui.md](./fair-price-ui.md).
- The full-dataset model benchmark is implemented under `app/evaluation/` and
  documented in [fair-price-evaluation.md](./fair-price-evaluation.md).

## Limitations

This is an analytical estimate, not an official HDB valuation. Comparable location
similarity currently uses block, street and town fields; listing coordinates are not
used by the comparable selector. Confidence decreases when evidence is sparse,
old, dissimilar or requires relaxed filters.
