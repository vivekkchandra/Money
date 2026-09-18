# One-stock qualification preparation

Use the saved live universe, not a manually maintained stock catalogue. A work
priority is recorded in `data/qualified/local-inference/inputs/first-qualification-selection.json`
with `trading212_id` and a factual `rationale`. Optional discovery notes are not
admitted evidence. Selection changes neither universe membership nor ranking.

```sh
MONEY_QLIB_ENABLED=false uv run python scripts/prepare_first_qualification.py
```

This secret-free offline check rehashes live provenance, normalizes the complete
raw universe to check identity conflicts, verifies the chosen provider/report/
dataset bytes and current cache mapping, and writes preparation/LEAN-readiness
outputs. It performs no external requests and grants no eligibility. Expired
provider evidence is labelled historical, not silently renewed. Expired broker
membership blocks preparation and cannot be fixed by a provider retry.

For a bounded retry of **only the selected stock** from the credentialed Mac:

```sh
railway run --service Money --environment production sh -c 'MONEY_QLIB_ENABLED=false MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/prepare_first_qualification.py --refresh-providers --max-requests 12'
```

The existing read-only provider transport enforces endpoint allowlists, rate
limits, cache integrity and secret filtering. The command requires a current,
matching Trading 212 credential binding before provider network work, uses the
qualification lock, and limits network requests to 1–12. This is an HTTP request
budget, not EODHD's weighted credit allowance. It does not advance the all-stock
cursor, refresh Trading 212, change reviews/the master universe, run research
engines or create a manifest. Its successful exit means **preparation only**.

Read the generated `outputs/FIRST_STOCK_NEXT.md`, `ACCOUNT_SCOPE_REVIEW.md`,
`PROVIDER_RIGHTS_REVIEW.md`, `EODHD_ACCESS_NEXT.md` and
`native-runtime-readiness.json` for the real remaining work. Do not interpret
public issuer/register discovery as an authenticated provider qualification or
a document-data licence. Proposed filing selections are kept separate from
existing operator inputs until deliberately reviewed and adopted.

The existing admission and runner remain authoritative. Snapshot creation
freezes all genuinely qualified members before screening; independent
TradingAgents/AI-Hedge-Fund reports still precede FIRST_PASS_LOCKED. Qlib may be
disabled explicitly, but LEAN is mandatory and cannot run without that lock and
its separate historical/runtime/study evidence. CIO/Red Team, release and hosted
acceptance are unchanged. Local Ollama is never Railway-hosted inference.
