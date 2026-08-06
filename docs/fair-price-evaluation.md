# Fair-price model evaluation

NearHome production now uses `CATBOOST` as the primary fair-price estimator.
The transparent weighted-comparable engine remains active as supporting
evidence and as an explicit runtime fallback if the prebuilt CatBoost artifact cannot load or validate.

## Commands

```bash
cd apps/api
python -m app.evaluation.run_model_benchmark --mode smoke
python -m app.evaluation.run_model_benchmark --mode full
pytest app/tests/test_model_evaluation.py -q
```

Smoke mode samples each temporal split for fast development feedback. It must
never be used as the reported benchmark. Full mode refuses sampling and prints
the raw, eligible, train, validation and final-test row counts before fitting.

## Controlled workflow

The Python workflow runs four bounded structured roles:

1. Experiment Designer creates the candidate, feature, split, metric and
   acceptance specification.
2. Leakage and Fairness Auditor checks target leakage, chronological splitting,
   untouched final-test data and failed-row accounting.
3. Model Implementer and Evaluator fits the baselines and ML candidates,
   records predictions/failures and chooses hybrid parameters from validation
   data only.
4. Independent Results Reviewer applies the predeclared final-test rules and
   emits one of the allowed recommendation states.

These are deterministic Python roles. LLM agents are not required to run the
benchmark and cannot change production selection.

## Data and leakage controls

The configured fixture is loaded in full. Invalid rows and exact duplicates are
reported, not silently discarded. Splits are chronological: the current full
run used training through `2024-07`, validation `2024-08`–`2025-07`, and an
untouched final test of `2025-08`–`2026-07`.

Comparable predictions use only transactions strictly before the prediction
month. ML preprocessing is fitted on training rows; final models are refit on
training plus validation only after model settings are fixed. All primary
models receive the same final-test row IDs.

The ML feature set contains floor area, storey midpoint, lease commencement,
lease months estimated at the historical transaction date, transaction month,
town, flat type and flat model. No resale price or future-derived field is a
feature.

## Artefacts

Each run creates `evaluation_outputs/<UTC timestamp>/` containing the
experiment specification, data audit, row reconciliation, feature matrix,
model configuration, overall and segment metrics, predictions, failures, hybrid
results, audit report, recommendation and run manifest.

## Full-run result recorded on 2026-08-03

The fixture contained 236,719 rows. All 236,719 were eligible: no exact
duplicates or invalid rows were found. The split was 186,007 training,
26,663 validation and 24,049 untouched final-test rows.

On the final test, weighted comparables had MAE S$43,170.31. CatBoost had MAE
S$36,041.11 (16.5% lower), median absolute error S$24,613.36, 86.1% within
±10%, and 100% prediction coverage. Random Forest had MAE S$36,860.77.
Linear Regression and median PPSM were worse than weighted comparables.

The predeclared reviewer produced `REPLACE_WITH_ML`, with CatBoost as the best
eligible candidate. That recommendation is now applied in production. The
weighted-average hybrid selected zero weighted-comparable weight on validation
and was consequently equivalent to CatBoost; the gated hybrid did not improve
the baseline.

The runtime model uses the same feature definitions and fixed hyperparameters
as the benchmark. It trains only on transactions before the valuation month,
is cached for the current transaction snapshot, and calibrates its displayed
range from the latest historical temporal holdout. Comparable rows, filter
status and weighted-comparable evidence remain visible in the response.

The earlier CatBoost failure could not be reconstructed: the repository has no
historical failed pipeline or exception trace, only `catboost_info` training
and metric artefacts. The current full run completed CatBoost training and
produced one finite prediction for every one of the 24,049 shared final-test
rows. The run records this as unconfirmed rather than inventing a cause.
