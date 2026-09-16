import { Badge, DataRecord, EmptyState, safeSourceUrl } from "./primitives";
import { parsePersonalRnd } from "@/lib/personal-rnd";
import { humanize, isTerminal, type Evidence, type Job, type RecordData } from "@/lib/contracts";

export function PersonalRndNotice({ className = "" }: { className?: string }) {
  return <aside className={`notice rnd-notice ${className}`} aria-label="Personal research and development mode"><strong>R&D / PERSONAL USE</strong><span>Real public-source data, possibly delayed. Not commercially licensed or qualified investment research. ISA eligibility may be unverified. No trade execution, recommendations or production signals.</span></aside>;
}

function provenance(item: RecordData) {
  const fields: RecordData = {};
  for (const key of ["id", "evidence_id", "provider", "kind", "content_hash", "publication_time", "retrieval_time", "point_in_time_status", "currency"] as const) {
    const value = item[key];
    if (typeof value === "string" && value.length <= 256) fields[key] = value;
  }
  if (item.historical_pit_verified === false) fields.historical_pit_status = "Not verified for historical decisions";
  return fields;
}

export function PersonalResearchDetail({ job, evidence, evidenceError, onRefresh }: { job: Job; evidence: Evidence | null; evidenceError: string | null; onRefresh: () => void }) {
  const packet = parsePersonalRnd(job.packet);
  const currency = packet?.analysis.raw_currency;
  const records = Array.isArray(evidence?.evidence) ? evidence.evidence.filter(item => item && typeof item === "object" && !Array.isArray(item)).slice(0, 100) : [];
  return <div className="personal-rnd-detail">
    <section className="research-header panel"><div><p className="eyebrow">PERSONAL RESEARCH RECORD</p><h2>{job.ticker}</h2><code>{job.id}</code></div><Badge value={job.status} /></section>
    <PersonalRndNotice />
    <section className="panel"><h2>Collection progress</h2><p role="status">{humanize(job.current_stage || job.status)}</p><p className="muted">A completed R&D collection is not a completed multi-firm validation or an investment signal.</p>{job.error_message && <p className="notice error" role="alert">{job.error_message}</p>}</section>
    {!packet ? <section className="panel"><EmptyState title={job.packet ? "R&D record could not be verified" : isTerminal(job.status) ? "No R&D result was produced" : "Research collection is in progress"} detail={job.packet ? "This response does not meet the personal R&D contract. No research conclusion is shown." : isTerminal(job.status) ? "This collection ended without a verified result. Review the recorded failure; no result is inferred." : "Available data and genuine component results will appear here after the worker persists them."} /><button className="text-button" onClick={onRefresh}>Refresh record</button></section> : <>
      <section className="panel"><p className="type-label fact">OBSERVED DATA · NOT A RECOMMENDATION</p><h2>Public-data observations</h2><p>Quote currency: <strong>{currency ?? "Not established"}</strong>. Raw values remain in their source currency; USD is not GBP, and 100 GBX equals £1 GBP. No foreign-exchange conversion is implied.</p><DataRecord data={packet.analysis} /><p className="small-print">These calculations describe the fetched data. They do not establish historical point-in-time availability, ISA eligibility or a qualified forecast.</p></section>
      <section className="panel"><h2>Actual component status</h2><p>Only the worker’s recorded outcomes are shown. Missing runtimes and incomplete research are not treated as successful firms.</p>{packet.components.length ? packet.components.map((component, index) => <details className="evidence-item" key={`${component.component}-${index}`} open><summary>{component.component} · {humanize(component.status)}</summary><p>{component.reason}</p></details>) : <p>No native research component results were recorded.</p>}</section>
      <section className="panel"><h2>Research boundary</h2><Badge value="INSUFFICIENT_EVIDENCE" /><p>No production signal was generated. The £200 research mandate and production eligibility, ethical, model, validation and audit gates remain unchanged.</p>{[...packet.limitations, ...packet.reasons].map((reason, index) => <p key={index} className="muted">{reason}</p>)}<details className="evidence-item"><summary>Immutable R&D record identity</summary><DataRecord data={{ research_id: packet.research_id, issued_at: packet.issued_at, snapshot_id: packet.snapshot_id, snapshot_hash: packet.snapshot_hash, purpose: packet.purpose }} /></details></section>
    </>}
    <section className="panel"><h2>Evidence and provenance</h2><p>Source identities, timestamps and hashes are shown below. Raw provider articles, filings and HTML are not redistributed here.</p>{evidenceError ? <p className="notice error" role="alert">{evidenceError}</p> : records.length ? records.map((item, index) => {
      const source = safeSourceUrl(item.source_url);
      return <details className="evidence-item" key={String(item.evidence_id ?? item.id ?? index)}><summary>{String(item.provider ?? "Source").slice(0, 128)} · {String(item.kind ?? "Evidence").slice(0, 128)}</summary><DataRecord data={provenance(item)} />{source && <a href={source} target="_blank" rel="noopener noreferrer">Open original source</a>}</details>;
    }) : <p role="status">No evidence has been persisted yet.</p>}</section>
  </div>;
}
