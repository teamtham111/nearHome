# NearHome Model Card — Fair Price

> Status: **Production model: CatBoost with weighted-comparable evidence/fallback**

## Model purpose

Estimate an analytical fair-price **range** for a shortlisted HDB resale flat using HDB transaction data. This is **not** an official HDB valuation.

## Production method

NearHome uses CatBoost as the primary central estimator. It uses floor area,
storey midpoint, lease commencement, remaining lease at the transaction date,
transaction month, town, flat type and flat model. Training is restricted to
transactions before the valuation month. The displayed range is calibrated from
a latest-period temporal holdout. Weighted comparables remain supporting,
buyer-visible evidence and the explicit fallback when CatBoost is unavailable.

## Inputs

- Transaction recency, town, block/street, normalized flat type, optional flat model,
  optional user-confirmed storey range, floor area, lease and market month
- Remaining lease is canonical integer months; `0` is treated as missing rather than as a valid lease.
  Legacy/display years are derived from months and are not parsed downstream.

Smart Paste retains labels such as `4A` and `5STD` as raw listing-subtype evidence,
then maps known codes to the canonical room category and HDB flat model (`4A` →
`4 ROOM`/`Model A`, `5STD` → `5 ROOM`/`Standard`). It never extracts or prefills
storey. Unknown or ambiguous codes remain without a model. When lease data is absent, the target block/street is matched to
HDB transaction records and lease evidence is resolved from exact-block expiry-month
medians, with commencement-year fallback only when transaction lease values are
unavailable. Town uses an authoritative geocoder/confirmed value first. If it
is absent, the confirmed block/street is matched against historical HDB records
using the shared canonical HDB address key; unresolved or ambiguous matches
remain unavailable rather than being guessed.

The standalone and hybrid challenger benchmark is deterministic and lives in
`apps/api/app/evaluation/`; its reports are generated outside the production
runtime. See [fair-price-evaluation.md](fair-price-evaluation.md).

## Excluded features

- Renovation/condition labels (unreliable)
- Grant-adjusted prices
- Asking price (explicitly excluded to prevent circular valuation)

## Evaluation and selection

- Time-based split: train (oldest) → validation diagnostics → final future-period production-selection set
- The model was selected using a chronological final-period comparison against
  alternative baselines and challengers.
- CatBoost achieved 16.5% lower final-test MAE than weighted comparables with
  100% prediction coverage in the recorded benchmark.
- The benchmark is an engineering selection check, not a guarantee of market accuracy.

## Uncertainty

Return `INSUFFICIENT_EVIDENCE` when the required lease/area/model inputs are
missing or the model cannot run and no comparable fallback is defensible. The
API returns the strongest comparable rows plus the actual filter-status object,
stage counts, relaxation steps and structured missing-feature warnings; full
candidate processing remains server-side. Missing storey or flat model is
informational and does not by itself prevent an estimate.

## Versioning

Each prediction stores the CatBoost model version, comparable-selection evidence,
calibration source, canonical lease months/source/confidence/as-of date and the
transaction data used. Refresh the transaction fixture and rerun the application
tests after changing the data source.

## Disclaimers

Displayed in UI at all times. Never labelled "HDB valuation" or "official value".
