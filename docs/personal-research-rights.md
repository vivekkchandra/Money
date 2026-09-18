# Personal research and provider rights

`MONEY_USAGE_MODE=personal_research` explicitly selects Money's restricted local
research policy, `money-provider-usage-v1`. The default is
`hosted_commercial_production`, including on a developer machine. Existing
production/research environment flags never silently select personal use.

Personal qualification records `UNVERIFIED_PERSONAL_USE`, not `APPROVED`, when no
current reviewed licence establishes the use. An unsigned
`inputs/provider-rights/{provider}.json` does not by itself block local personal
research. Existing reviews and licence evidence are preserved, not signed or
rewritten. The audit records provider, dataset/endpoint categories, attribution,
available terms/source references and all of these restrictions:

- No raw-data redistribution or public display.
- No resale or external sharing outside the local workflow.
- Retain source/provider attribution and original source references.

This product policy is not a claim that a provider granted a licence. Actual
access restrictions remain effective. It cannot supply missing datasets, change
subscription entitlement or override source integrity. A commercial/public
manifest and release still require current strict provider-rights approval;
personal results cannot become commercial-qualified through a mode change.
Unverified personal source text is not sent to remote inference. The local
Ollama endpoint may process it; local success does not qualify hosted inference.

## Run locally with Railway-injected credentials

From the Mac running Ollama, the following executes locally; it does not alter
Railway's service variables or expose Ollama:

```sh
railway run --service Money --environment production sh -c 'MONEY_USAGE_MODE=personal_research MONEY_QLIB_ENABLED=false MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/finalize_live_universe.py'
railway run --service Money --environment production sh -c 'MONEY_USAGE_MODE=personal_research MONEY_QLIB_ENABLED=false MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/build_live_qualification.py'
```

When issuer Fundamentals access is unavailable, prepare exact official sources
before the finalizer. The Nichols selection is an operator input, not a runtime
special case or identity approval:

```sh
railway run --service Money --environment production sh -c 'MONEY_USAGE_MODE=personal_research MONEY_QLIB_ENABLED=false MONEY_INFERENCE_CONFIG=data/configuration/ollama-inference.json uv run python scripts/prepare_issuer_evidence.py --selection inputs/issuer-sources/GB0006389398.json'
```

Only successful, hash-verified captures are consumed. A blocked download,
missing official identity attribute or incomplete group disclosure remains
unresolved. Discovery notes/browser summaries are not substituted for raw
source bytes. Allow any existing qualification command to finish before running
another command against the same output root; the runner lock is never bypassed.

`CAPTURE_INCOMPLETE` includes a `failures` list with source role, safe error code,
numeric HTTP status (when received) and retryability. It never includes response
bodies, authentication headers or exception text. Rerunning reuses unexpired,
hash-verified captures and retries missing sources. A null HTTP status means no
HTTP status was recorded, not an authentication rejection. `rights_approved=false`
is intentional: capture does not grant licensing approval. Even `CAPTURED` means
bytes were retrieved, not that identity or business coverage passed validation;
for example, a JavaScript-only security page cannot establish an ISIN.

Inspect `outputs/universe-review-tasks.json` for global source-use audit records,
`outputs/ethical-screenings.json` for one-pass issuer results and
`outputs/FIRST_STOCK_NEXT.md` for the actual next evidence blocker. Selecting
personal use does not promise that any instrument qualifies.

## Issuer evidence without a paid profile

EODHD Fundamentals is not uniquely required to establish legal issuer identity
or business activity. Exact official issuer, security/ISIN and Companies House
corroboration can establish company number, legal name and jurisdiction without
fuzzy matching. Captured disclosure bytes retain hashes, source URLs and actual
retrieval times. Unknown publication dates remain unknown, not invented PIT
evidence. Filing metadata alone is still not financial-document coverage.

Validated official issuer/business disclosures can feed the same one-pass
ethical screen. All configured exclusions are checked against genuine
issuer-wide evidence. A name, sector/SIC label, product description or absence of
keywords cannot establish PASS. Missing/incomplete evidence remains UNKNOWN;
supported prohibited exposure remains FAIL. Only PASS proceeds and is reused
for its configured validity period.

Live broker membership, exact identity, freshness, financial/corporate-action/
spread/cost/PIT evidence, native/security qualification and snapshot integrity
remain unchanged. Qlib stays explicitly disabled. TradingAgents and AI Hedge
Fund must produce independent sealed reports before FIRST_PASS_LOCKED; LEAN is
mandatory before CIO/Red Team. Release and hosted acceptance remain separate.
