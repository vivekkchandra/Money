"use client";

import Link from "next/link";
import { currentUniverse, type UniversePage } from "@/lib/universe";
import { DataRecord, EmptyState } from "./primitives";

export function UniverseRecords({ page, now }: { page: UniversePage; now: number }) {
  if (page.coverage === "previously_researched_only") return <>
    <p className="muted">Historical eligibility checks from previous research requests—not a current or complete broker directory. A saved record does not establish present ISA eligibility.</p>
    {page.instruments.length ? page.instruments.map((item, index) => <details className="evidence-item" key={String(item.id ?? index)}><summary>Historical eligibility record {index + 1}<span>{String(item.created_at ?? "")}</span></summary><DataRecord data={item.payload && typeof item.payload === "object" ? item.payload as Record<string, unknown> : item} />{typeof item.job_id === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(item.job_id) && <Link className="text-button" href={`/research/${item.job_id}/evidence`}>Inspect historical research evidence →</Link>}</details>) : <EmptyState title="No historical eligibility records" detail="No verified current universe is inferred from an empty research history." icon="search" />}
  </>;
  const current = currentUniverse(page, now), expired = page.instruments.length - current.length;
  return <>
    <p className="muted">Reviewed manifest subset: {page.total} mandate-eligible instruments at the last evaluation. This is not the complete Trading 212 ISA universe. Current verified GBP / GBX individual stocks only; research admission rechecks all gates.</p>
    <p className="small-print universe-provenance">Coverage evaluated <time dateTime={page.evaluated_at}>{page.evaluated_at}</time> · catalogue hash <code>{page.catalogue_hash}</code></p>
    {!!expired && <div className="notice" role="status">{expired} expired verification {expired === 1 ? "record is" : "records are"} hidden. Refresh to check current coverage.</div>}
    {current.length ? current.map(item => <details className="evidence-item" key={item.instrument_id}><summary>{item.company} · {item.ticker}<span>{item.currency} · ISA eligibility verified</span></summary><DataRecord data={{ instrument_type: item.instrument_type, exchange: item.exchange, raw_quote_currency: item.currency, verified_at: item.verified_at, verified_until: item.verified_until, source: item.source, provider: item.provider, source_id: item.source_id, metadata_hash: item.metadata_hash, eligibility_proof_hash: item.eligibility_proof_hash, ethical_proof_hash: item.ethical_proof_hash }} /><Link className="text-button" href={`/jobs?ticker=${encodeURIComponent(item.ticker)}`}>Review company for research →</Link></details>) : <EmptyState title="No current verified instruments on this page" detail="Money fails closed when ISA eligibility or ethical verification is missing or stale. No public listing is substituted for verified availability." icon="search" />}
  </>;
}
