# Token and compute strategy

Use Graphify queries of 400–800 tokens before narrow upstream source reads. Keep an audit in GRAPHIFY_DISCOVERY.md; never rebuild unchanged upstream indexes.

Compute indicators, normalization, returns, ranges, risk statistics, hashes, gates and deduplication in deterministic code. Qlib uses numerical models, not invented personas. No LLM calculates arithmetic that Python can verify.

Expensive firms run only after eligibility, coverage and quality screening. Reuse immutable snapshot evidence and cached provider responses by content hash. LLM context contains relevant excerpts and source IDs, not raw repositories or all filings. Activate CIO specialists conditionally. Cap cross-examination at two rounds. Persist model/prompt versions and token/cost usage; missing usage is reported as unknown rather than fabricated zero spend.

`TokenBudgetManager` enforces per-job/candidate/stage/agent/day/provider-model
limits transactionally. Missing first-pass roles are admitted as one batch;
failed admission rolls back. CIO reserves only when reached. Persisted usage is
reconciled on resume; lost unmeasured invocations retain their reserved maximum.
Cost remains unknown without both measured usage and explicit configured rates.

Live configuration preflights maximum native call counts and conservative UTF-8
input bounds. It never silently switches models or reduces roles to fit a budget.
No-discovery candidates incur no qualitative calls; Qlib numerical screening is
reused privately by its first-pass firm. General provider-response caching and a
richer adaptive quant-only shortlist policy are not implemented yet.

Qualitative context uses `money-qualitative-latest80-v1`: latest 80 price bars and
all non-price evidence, with original/selected/omitted counts, selected IDs and
the full immutable snapshot hash. Every full-snapshot record is still checked for
PIT/conflicts before selection. Native qualitative claims cannot cite omitted
records. Oversized retained documents fail rather than silently truncating them.
Qlib, LEAN and deterministic auditors retain the full frozen history.
