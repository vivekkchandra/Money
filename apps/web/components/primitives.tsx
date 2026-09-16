import Link from "next/link";
import { humanize, type Job, type RecordData } from "@/lib/contracts";

export function Icon({ name = "grid", size = 20 }: { name?: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    sliders: <><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="8" cy="6" r="2" /><circle cx="16" cy="12" r="2" /><circle cx="10" cy="18" r="2" /></>,
    search: <><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6M10 7v6M7 10h6" /></>,
    layers: <><path d="m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5" /></>,
    signal: <><path d="M4 19V13M10 19V9M16 19V5M22 19V2" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    chart: <><path d="M3 3v18h18M6 16l5-6 4 3 6-8" /></>,
    pulse: <><path d="M2 12h5l3-8 4 16 3-8h5" /></>,
    arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V6a4 4 0 0 1 8 0v4M12 14v3" /></>,
    check: <path d="m5 12 4 4L20 5" />,
    plus: <path d="M12 5v14M5 12h14" />,
    document: <><path d="M14 2H5v20h14V7l-5-5ZM14 2v6h5M8 12h8M8 16h6" /></>,
    logout: <><path d="M9 3H3v18h6M12 12h9m-4-4 4 4-4 4" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.grid}</svg>;
}

export function Badge({ value, label }: { value: string; label?: string }) {
  const tone = /FAIL|REJECT|VETO|EXPIRED|UNAVAILABLE|ERROR/i.test(value) ? "danger" : /PASS|COMPLETE|CANDIDATE|HEALTHY|READY|OK/i.test(value) ? "positive" : /WARN|INSUFFICIENT|DEGRADED|UNKNOWN|SEALED|PENDING/i.test(value) ? "warning" : "neutral";
  return <span className={`badge ${tone}`}><span className="status-dot" />{label ?? humanize(value)}</span>;
}

export function EmptyState({ title, detail, icon = "document", children }: { title: string; detail: string; icon?: string; children?: React.ReactNode }) {
  return <div className="empty-state"><div className="empty-icon"><Icon name={icon} size={28} /></div><h3>{title}</h3><p>{detail}</p>{children}</div>;
}

export function JobList({ jobs }: { jobs: Job[] }) {
  if (!jobs.length) return <EmptyState title="Your next idea starts here" detail="Request research on a stock to create a durable research record. Eligibility and evidence are checked before the firms begin." icon="search" />;
  return <div className="job-list">{jobs.map((job) => <Link className="job-row" href={`/research/${job.id}`} key={job.id}><div className="ticker-mark">{job.ticker.slice(0, 2)}</div><div className="job-name"><strong>{job.ticker}</strong><span>{humanize(job.current_stage || job.status)}</span></div><Badge value={job.status} /><span className="job-date">{new Date(job.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</span><Icon name="arrow" size={17} /></Link>)}</div>;
}

export function safeSourceUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function DataValue({ value, evidenceHref, depth = 0 }: { value: unknown; evidenceHref?: string; depth?: number }) {
  if (depth > 8) return <span className="muted">Nested record exceeds the display limit</span>;
  if (value === null || value === undefined) return <span className="muted">Not available</span>;
  if (typeof value === "boolean") return <span>{value ? "Yes" : "No"}</span>;
  if (Array.isArray(value)) return value.length ? <div className="data-array">{value.slice(0, 100).map((item, index) => <div key={index}><DataValue value={item} evidenceHref={evidenceHref} depth={depth + 1} /></div>)}{value.length > 100 && <p>Showing the first 100 records.</p>}</div> : <span className="muted">None recorded</span>;
  if (typeof value === "object") return <DataRecord data={value as RecordData} evidenceHref={evidenceHref} depth={depth + 1} />;
  return <span className="data-text">{String(value).slice(0, 20000)}{String(value).length > 20000 ? "… [display limit]" : ""}</span>;
}

export function DataRecord({ data, evidenceHref, depth = 0 }: { data: RecordData; evidenceHref?: string; depth?: number }) {
  // Raw licensed documents are not redistributed; show their facts and source links.
  const excluded = /^(?:raw_content|raw_text|document_text|html|api_key|password|session_secret|access_token)$/i;
  return <dl className="data-record">{Object.entries(data).filter(([key]) => !excluded.test(key)).slice(0, 100).map(([key, value]) => {
    const source = /^(?:url|source_url|document_url|canonical_url)$/.test(key) ? safeSourceUrl(value) : null;
    return <div key={key}><dt>{humanize(key)}</dt><dd>{source ? <a className="source-link" href={source} target="_blank" rel="noopener noreferrer">Open source ({new URL(source).hostname}) ↗</a> : key === "evidence_ids" && evidenceHref && Array.isArray(value) ? <div className="citation-list">{value.slice(0, 100).map((id) => <Link key={String(id)} href={`${evidenceHref}#evidence-${encodeURIComponent(String(id))}`}>{String(id)}</Link>)}</div> : <DataValue value={value} evidenceHref={evidenceHref} depth={depth} />}</dd></div>;
  })}</dl>;
}

export function SectionHeading({ kicker, title, action }: { kicker?: string; title: string; action?: React.ReactNode }) {
  return <div className="section-heading"><div>{kicker && <p className="eyebrow">{kicker}</p>}<h2>{title}</h2></div>{action}</div>;
}

export function FirmReportCard({ name, letter, description, locked, report, evidenceHref }: { name: string; letter: string; description: string; locked: boolean; report?: RecordData; evidenceHref?: string }) {
  return <article className="panel firm-card"><div className="firm-top"><span className="firm-letter">{letter}</span><span className={`type-label ${letter === "C" ? "inference" : "opinion"}`}>{letter === "C" ? "QUANT EVIDENCE" : "FIRM OPINION"}</span></div><h2>{name}</h2><p>{description}</p><Badge value={locked && report ? "COMPLETE" : "SEALED"} />{locked && report ? <details><summary>Read independent report <Icon name="arrow" size={15} /></summary><DataRecord data={report} evidenceHref={evidenceHref} /></details> : <div className="sealed-note"><Icon name="lock" size={15} />Awaiting the blind-report barrier</div>}</article>;
}
