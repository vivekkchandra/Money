# Offline baseline training — 2026-09-16

Status: implemented offline engineering, **not production model qualification**.

## Implemented

`money.models.training` accepts bounded immutable numeric datasets in the exact
five-feature order consumed by `QlibNativeRunner`. Money-owned NumPy ridge uses
train-only centering/scaling and exports raw-space coefficients and an intercept
into `LinearModelArtifact`. This is **not native Qlib training**. No upstream
source was read or changed for this work.

The workflow enforces UTC feature observation/original-availability/prediction
timestamps and future label start/end/availability. Unique instrument/feature
windows and prediction timestamps prevent duplicates. Folds split distinct
timestamp groups: same-time instruments cannot straddle train/validation.

Three to ten expanding chronological folds use a predeclared embargo. Training
labels must end and become available strictly before each fit cutoff. Fold
outcomes cannot overlap the next fit. A final held-out evaluation never informs
preprocessing, parameter selection, or a post-evaluation refit. Available test
outcomes are thinned to non-overlapping intervals per instrument; excluded and
purged counts remain visible. RMSE, MAE, directional accuracy, defined R² and a
training-mean baseline are diagnostics, not calibrated confidence or after-cost
investment outcomes.

Inputs are bounded to 32 MB regular JSON, 20,000 rows, five finite bounded numeric
features, 21 distinct feature evidence hashes, and a 1–30-day target horizon.
Symlink/nonregular files are denied. Output goes to a new private directory and
never overwrites a previous experiment.

Reproducibility records dataset/config hashes, exact artifact/report byte hashes,
per-fit row hashes/coefficients/cutoffs, training and feature implementation hashes,
NumPy/Python versions, and declared source/universe/corporate-action archive hashes.
Cross-platform numerical rounding may differ; runtime qualification is separate.

## Operator workflow

1. Curate an independently reviewed historical archive with original publication
   availability, historical eligibility and corporate-action adjustment policy.
2. Use `observation_from_evidence(ticker, prediction_time, feature_records,
   label_open, label_close)` to recompute features from exactly 21 Money bars. It
   normalizes GBP/GBX and rejects mixed currencies. The label is next-entry-bar
   open to selected horizon-bar close price return. Preserve source records.
3. Build `TrainingDataset` and predeclare `TrainingConfiguration` JSON before
   inspecting held-out outcomes. Synthetic inputs are explicitly marked.
4. Run only offline:

   ```bash
   uv run python scripts/train_baseline.py \
     --dataset /reviewed/archive/training-dataset.json \
     --config /reviewed/experiments/predeclared-config.json \
     --output /reviewed/experiments/new-baseline-v1
   ```

5. Preserve the three outputs: `model-artifact.json`, `validation-report.json`,
   `training-result.json`, plus source archive and predeclared configuration.
6. Obtain independent source/PIT, regression and methodology review. Only an
   authorized operator may later prepare real validation/approval evidence and
   use the existing manually controlled registry.

The trainer never contacts providers, constructs approval/promotion evidence,
registers models, or activates production versions. No public API exposes it.

## Production evidence boundary

Numeric rows and archive hashes are operator inputs. Timestamp consistency alone
cannot establish source authenticity, historical universe correctness, lack of
survivorship bias, correct adjustments or an untouched holdout. Output remains
`UNPROMOTED`, `pit_validated=False`, `production_qualified=False` and
`independent_approval=False`. Native Qlib qualification and registry promotion
explicitly reject artifacts whose PIT status is not true.

An independently reviewed final artifact with `pit_validated=True` has a **new
artifact hash**. All independent validation and approval records must reference
that exact final artifact and report bytes; the old experiment hash cannot be
silently reused. This transition is not performed by the trainer.

Walk-forward measures successive fits of a fixed procedure, not identical final
coefficients at every date. Cross-instrument dependence and regime coverage are
not certified. Reusing the same holdout for model selection requires a new
independent holdout. Synthetic regression success proves no live predictive skill.

## Verification

| Requirement | Status | Evidence | Environment | Blocker |
| --- | --- | --- | --- | --- |
| Offline fit / chronology / CLI | VERIFIED | 20 tests in `tests/unit/test_baseline_training.py` pass, using actual NumPy fits and a CLI subprocess | Local Python 3.12, explicitly synthetic data | No live qualification implied |
| OOS/fold isolation | VERIFIED | OOS label changes preserve final coefficients/intercept; post-fold data changes preserve earlier fits; late labels and exact boundary purge tested | Unit tests | None |
| Artifact safety | VERIFIED | NaN/Inf, duplicate UTC instants/windows, chronology violations, hash changes, insufficient samples, bounded files and overwrite rejection tested | Unit tests | None |
| Native regression | VERIFIED | Training/native/upstream/opt-in suites: 94 passed, 5 explicit missing-opt-in credential skips; existing CrewAI warnings remain | Local | Native production qualification remains blocked |
| Registry promotion safety | VERIFIED | New unqualified-artifact test and existing production-service regressions: 11 passed; even otherwise hash-bound approval cannot activate PIT-false model | Local SQLite test substitute | PostgreSQL verification belongs to the main integration run |
| Ruff/mypy | VERIFIED | Ruff clean for new source/tests and native PIT gate; mypy clean for training, CLI and native Qlib module | Local | None |
| Production model qualification | BLOCKED_CREDENTIAL | No authoritative qualified dataset or independent approval supplied; no production model trained/promoted | Not run | Reviewed source/PIT/universe/action archive and manual approval |
| Native Qlib training | NOT_IMPLEMENTED | Deliberately Money NumPy training with existing native Qlib inference contract | Offline | Native inference runtime qualification remains separate |

## Discovery / skills

Money Graphify query: `Qlib linear model artifact promotion training feature
availability walk forward`, 600-token budget. Exact Money seams inspected:
`adapters/native_qlib.py`, `offline_research/promotion.py`, `models/registry.py`
and the numeric input contracts in `adapters/upstream.py`. Parent owns graph update.

The ML pipeline skill shaped data validation, fit/evaluation separation,
reproducibility and manual promotion. Its optional reference/asset directories are
absent in the installed package; the core workflow and existing contracts were
used without adding a framework. Python testing patterns guided explicit
synthetic fixtures, adversarial parameterization and CLI subprocess tests.

The solver API was checked against current
[official NumPy documentation](https://numpy.org/doc/stable/reference/generated/numpy.linalg.solve.html).
