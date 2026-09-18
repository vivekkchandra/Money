"use client";

import { useState, type ChangeEvent } from "react";
import { parseResearchTesting, RESEARCH_STATE_LABELS, type ResearchTestingView } from "@/lib/research-admission";

export function ResearchTestingSummary({ result }: { result: ResearchTestingView }) {
  const shown = (value: number | null) => value === null ? "Not recorded" : value.toLocaleString("en-GB");
  return <section className="panel" aria-label="Local research testing summary">
    <p className="eyebrow">LOCAL FILE · RESEARCH / TESTING ONLY</p>
    <h2>{RESEARCH_STATE_LABELS[result.state]}</h2>
    <p role="status">{result.status}</p>
    <p>This operator-provided file is not server-verified production evidence. Research admission does not establish ethical approval, commercial licensing, buyability or safety to trade. No orders or release approval are provided here.</p>
    <dl><dt>Trading 212 instruments</dt><dd>{shown(result.rawInstruments)}</dd><dt>GBP / GBX stocks</dt><dd>{shown(result.gbpGbxStocks)}</dd><dt>Research eligible</dt><dd>{shown(result.researchEligible)}</dd><dt>Basic identity conflicts</dt><dd>{shown(result.identityConflicts)}</dd></dl>
    <h3>Optional enrichment coverage</h3><p>Coverage is informational, never a research-admission gate. Missing fundamentals, Companies House or ethical screening do not block independent research.</p>
    {result.enrichment.length ? result.enrichment.map(item => <details key={item.source}><summary>{item.source.replaceAll("_", " ")}</summary><dl>{Object.entries(item.counts).map(([status, count]) => <div key={status}><dt>{status.replaceAll("_", " ")}</dt><dd>{shown(count)}</dd></div>)}</dl></details>) : <p>No enrichment counts were recorded.</p>}
    <h3>Recorded candidate progress</h3>{result.instruments.length ? result.instruments.map(item => <article key={item.trading212Id}><h4>{item.trading212Id}</h4><p>{RESEARCH_STATE_LABELS[item.state]}</p><p>Independent reports recorded: {item.independentReports}. LEAN execution recorded: {item.leanExecuted ? "Yes" : "No"}.</p><p>Ethics annotation: {item.ethicalStatus ?? "Not recorded"}. Not a research gate.</p></article>) : <p>No independent agent runs are recorded.</p>}
    <p className="small-print">Qlib disabled. Genuine LEAN execution remains mandatory for LEAN_VALIDATED. Raw licensed provider data and original reports are not displayed or uploaded by this viewer.</p>
  </section>;
}

export function LocalResearchArtifactViewer() {
  const [result, setResult] = useState<ResearchTestingView | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function load(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    setResult(null); setError(null);
    if (!file) return;
    try {
      if (file.size > 2_000_000) throw new Error("bound");
      setResult(parseResearchTesting(JSON.parse(await file.text())));
    } catch {
      setError("This file is not a valid bounded local research/testing summary. No approval is inferred.");
    }
  }
  return <><section className="panel"><h2>Inspect a local research/testing run</h2><p>Choose <code>outputs/research-testing-result.json</code> from the local workflow. It is read in this browser only, not uploaded or treated as the result of this saved job.</p><label>Local research result JSON<input type="file" accept="application/json,.json" onChange={load} /></label>{error && <p className="notice error" role="alert">{error}</p>}</section>{result && <ResearchTestingSummary result={result} />}</>;
}
