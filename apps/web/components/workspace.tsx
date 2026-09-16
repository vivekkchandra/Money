"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { DEFAULT_MANDATE, humanize, isTerminal, JOB_STAGES, signalState, unwrapSignal, type Evidence, type Health, type Job, type Mandate, type RecordData, type Reports } from "@/lib/contracts";
import { Badge, DataRecord, DataValue, EmptyState, FirmReportCard, Icon, JobList, SectionHeading } from "./primitives";

const NAV = [
  ["", "Overview", "grid"], ["mandate", "Research mandate", "sliders"], ["discovery", "Candidate discovery", "search"], ["jobs", "Research jobs", "layers"],
  ["signals", "Research signals", "signal"], ["expired", "Expired research", "clock"], ["outcomes", "Outcomes", "chart"], ["health", "System health", "pulse"],
];
const TITLES: Record<string, [string, string]> = {
  "": ["A clearer view. An independent perspective.", "Your research workspace"],
  mandate: ["Define the boundaries.", "Research mandate"],
  discovery: ["More than one way to find an idea.", "Candidate discovery"],
  jobs: ["Follow the evidence as it develops.", "Research jobs"],
  signals: ["Research worth a closer look.", "Research signals"],
  expired: ["A record, not an active setup.", "Expired research"],
  outcomes: ["Measure what the research got right.", "Outcome intelligence"],
  health: ["A clear view of the system.", "System health"],
  research: ["Independent work. Open to scrutiny.", "Candidate research"],
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, cache: "no-store", headers: { "Content-Type": "application/json", ...init?.headers } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ?? "The request could not be completed");
  return data as T;
}

function useResource<T>(path: string | null, refresh = 0, poll = false) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedPath, setLoadedPath] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let active = true;
    async function load() {
      try {
        const result = await api<T>(path!, { signal: controller.signal });
        if (active) { setData(result); setError(null); setLoadedPath(path); }
      } catch (reason) {
        if (active && !controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "Unable to load research"); setLoadedPath(path); }
      }
    }
    void load();
    const timer = poll ? setInterval(() => void load(), 5000) : undefined;
    return () => { active = false; controller.abort(); if (timer) clearInterval(timer); };
  }, [path, refresh, poll]);
  return { data: loadedPath === path ? data : null, error: loadedPath === path ? error : null, loading: !!path && loadedPath !== path };
}

export function Workspace({ view }: { view: string[] }) {
  const page = view[0] ?? "";
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const session = useResource<{ configured: boolean; authenticated: boolean }>("/api/session", revision);
  const authenticated = session.data?.authenticated === true;
  const jobsResource = useResource<{ jobs: Job[] }>(authenticated ? "/api/research" : null, revision, true);
  const health = useResource<Health>(authenticated ? "/api/health" : null, revision, true);
  const jobs = jobsResource.data?.jobs ?? [];
  const active = jobs.filter((job) => !isTerminal(job.status));
  const [title, eyebrow] = TITLES[page] ?? ["This page is outside the research universe.", "Page not found"];

  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to content</a>
    <aside className="sidebar">
      <Link href="/" className="brand" aria-label="Money home"><span className="brand-symbol">m<span>·</span></span><span>money<span className="brand-period">.</span></span></Link>
      <p className="sidebar-caption">INDEPENDENT BY DESIGN</p>
      <div className="workspace-label"><span className="workspace-avatar">P</span><div>Personal workspace<small>UK equity research</small></div><Icon name="lock" size={15} /></div>
      <p className="nav-label">WORKSPACE</p>
      <nav aria-label="Main navigation">{NAV.map(([path, label, icon], index) => <Link href={`/${path}`} key={label} className={`nav-link ${page === path || (page === "research" && path === "jobs") ? "selected" : ""} ${index === 4 ? "nav-divider" : ""}`} aria-current={page === path ? "page" : undefined}><Icon name={icon} size={18} /><span>{label}</span>{path === "jobs" && active.length > 0 && <span className="nav-count">{active.length}</span>}</Link>)}</nav>
      <div className="sidebar-bottom"><div className="manual-note"><Icon name="lock" size={18} /><div><strong>Your decisions. Always.</strong><p>Evidence and perspective.<br />Every investment decision stays yours.</p></div></div><div className="sidebar-footer"><span className="status-dot" />Research workspace<span>v0.1</span></div></div>
    </aside>
    <div className="main-column">
      <header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>{eyebrow}</strong></div><div className="topbar-right"><span className="universe-pill">UK · GBP / GBX</span>{authenticated && <button className="icon-button" aria-label="Sign out" onClick={async () => { await api("/api/session", { method: "DELETE" }); refresh(); }}><Icon name="logout" size={18} /></button>}<span className="profile-avatar">P</span></div></header>
      <main id="main">
        <div className="page-heading"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1></div><span className="research-only"><Icon name="lock" size={14} />Research only</span></div>
        {session.loading ? <div className="notice" role="status">Opening your workspace…</div> : !authenticated ? <SignIn configured={session.data?.configured === true} error={session.error} onSuccess={refresh} /> : <>
          {jobsResource.error && <div className="notice error" role="alert">{jobsResource.error}<button onClick={refresh}>Retry</button></div>}
          {health.data?.mode === "demo" && <div className="notice demo"><strong>Demonstration environment</strong> Research uses synthetic fixtures. Use DEMO.L to explore the workflow. Reports are not live firm research or investment opportunities.</div>}
          {page === "" && <Dashboard jobs={jobs} active={active} health={health.data} onCreated={refresh} />}
          {page === "mandate" && <MandateEditor />}
          {page === "jobs" && <><ResearchRequest onCreated={refresh} /><section className="panel"><SectionHeading title="Research activity" action={<button className="text-button" onClick={refresh}>Refresh <Icon name="arrow" size={15} /></button>} /><JobList jobs={jobs} /></section></>}
          {page === "research" && <ResearchDetail id={view[1]} revision={revision} />}
          {page === "discovery" && <Discovery revision={revision} />}
          {(page === "signals" || page === "expired") && <Signals expired={page === "expired"} revision={revision} />}
          {page === "outcomes" && <Outcomes revision={revision} />}
          {page === "health" && <HealthView health={health.data} error={health.error} onRefresh={refresh} />}
          {!TITLES[page] && <EmptyState title="Page not found" detail="Return to the dashboard to continue your research."><Link className="button" href="/">Go to overview</Link></EmptyState>}
        </>}
        <footer className="page-footer"><span>Money · Independent investment research</span><span>Evidence before confidence.</span></footer>
      </main>
    </div>
  </div>;
}

function SignIn({ configured, error, onSuccess }: { configured: boolean; error: string | null; onSuccess: () => void }) {
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true); setMessage(null);
    try { await api("/api/session", { method: "POST", body: JSON.stringify({ password }) }); setPassword(""); onSuccess(); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "Sign-in failed"); }
    finally { setPending(false); }
  }
  return <div className="welcome-grid"><section className="welcome-hero"><p className="eyebrow">A RESEARCH COLLECTIVE</p><h2>More perspectives.<br />Better questions.</h2><p>Three independent firms investigate the same evidence. A validation laboratory tests their hypotheses. A separate investment office challenges the conclusions.</p><div className="firm-tokens"><span>TradingAgents</span><span>ai-hedge-fund</span><span>Qlib</span></div><div className="welcome-line"><Icon name="lock" size={17} />Shared facts. Independent opinions.</div></section><section className="panel sign-in"><span className="empty-icon"><Icon name="lock" size={25} /></span><h2>Your private research workspace</h2><p className="muted">Sign in to review evidence, follow research and investigate new ideas.</p>{configured ? <form onSubmit={submit}><label htmlFor="password">Workspace password</label><input id="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required maxLength={512} /><button className="button full-width" disabled={pending}>{pending ? "Signing in…" : "Open workspace"}<Icon name="arrow" size={17} /></button></form> : <div className="notice">{error ?? "This workspace is awaiting secure configuration. Research access will become available once the workspace owner completes setup."}</div>}{message && <p className="form-error" role="alert">{message}</p>}<p className="small-print">Trading 212 provides an eligibility constraint only. Money never accesses your portfolio or executes trades.</p></section></div>;
}

function Dashboard({ jobs, active, health, onCreated }: { jobs: Job[]; active: Job[]; health: Health | null; onCreated: () => void }) {
  return <>
    <section className="overview-hero"><div><p className="eyebrow">YOUR RESEARCH, WITH PERSPECTIVE</p><h2>Conviction begins<br />with better evidence.</h2><p>Independent analysis. A rigorous challenge.<br />Space for your own judgement.</p><Link href="/jobs" className="button light">Start researching<Icon name="arrow" size={17} /></Link></div><div className="research-orbit" aria-label="Three independent firms feed a shared audit"><span className="orbit-center">m<span>·</span></span><span className="orbit-node node-one">TradingAgents<small>RESEARCH FIRM A</small></span><span className="orbit-node node-two">ai-hedge-fund<small>RESEARCH FIRM B</small></span><span className="orbit-node node-three">Qlib<small>QUANT FIRM C</small></span><span className="orbit-caption">INDEPENDENT PERSPECTIVES · SHARED FACTS</span></div></section>
    <div className="stats-grid"><Stat label="Active research" value={String(active.length).padStart(2, "0")} note="Independent investigations" icon="layers" /><Stat label="Completed reviews" value={String(jobs.filter((job) => job.status === "COMPLETE").length).padStart(2, "0")} note="Research records, not endorsements" icon="document" /><Stat label="Capital assumption" value="£200" note="Maximum · research only" icon="sliders" /><Stat label="Research horizon" value="1–30" note="Days · short-horizon evidence" icon="clock" /></div>
    <div className="content-grid"><section className="panel"><SectionHeading kicker="THE RESEARCH DESK" title="Recent investigations" action={<Link className="text-button" href="/jobs">View all <Icon name="arrow" size={15} /></Link>} /><JobList jobs={jobs.slice(0, 5)} /></section><section className="panel mandate-summary"><p className="eyebrow">YOUR NORTH STAR</p><h2>A focused mandate.</h2><div className="mandate-line"><span>Universe</span><strong>Trading 212 ISA</strong></div><div className="mandate-line"><span>Instruments</span><strong>Individual stocks</strong></div><div className="mandate-line"><span>Quote currency</span><strong>GBP / GBX</strong></div><div className="ethical-note"><Icon name="check" size={17} /><span>Defence, weapons and oil activities excluded.</span></div><Link href="/mandate" className="text-button">Review mandate <Icon name="arrow" size={15} /></Link></section></div>
    <ResearchRequest onCreated={onCreated} />
    <section className="protocol-panel"><div><p className="eyebrow">HOW MONEY THINKS</p><h2>Agreement is only the beginning.</h2><p>Research reaches you after independent analysis, historical validation and an adversarial review.</p></div><div className="protocol-steps"><span><b>01</b>Shared evidence</span><span><b>02</b>Blind research</span><span><b>03</b>Lock & challenge</span><span><b>04</b>Evidence consensus</span></div></section>
    {health && <p className="connection-note"><span className="status-dot" /> Research service: {health.status} · {health.mode} environment</p>}
  </>;
}

function Stat({ label, value, note, icon }: { label: string; value: string; note: string; icon: string }) {
  return <section className="stat-card"><div><span>{label}</span><Icon name={icon} size={18} /></div><strong>{value}</strong><small>{note}</small></section>;
}

function loadMandate(): Mandate {
  try {
    const stored = localStorage.getItem("money.mandate");
    if (stored) return { ...DEFAULT_MANDATE, ...JSON.parse(stored) };
  } catch { /* A draft is optional; canonical validation runs on the server. */ }
  return { ...DEFAULT_MANDATE };
}

function ResearchRequest({ onCreated }: { onCreated: () => void }) {
  const [ticker, setTicker] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Job | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true); setError(null); setCreated(null);
    try { const job = await api<Job>("/api/research", { method: "POST", body: JSON.stringify({ ticker: ticker.trim().toUpperCase(), mandate: loadMandate() }) }); setCreated(job); setTicker(""); onCreated(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to create research"); }
    finally { setPending(false); }
  }
  return <section className="request-panel"><div><p className="eyebrow">FOLLOW YOUR CURIOSITY</p><h2>What are you researching?</h2><p>Start with a ticker. Money checks the mandate before investigating.</p></div><form onSubmit={submit}><label className="sr-only" htmlFor="ticker">Stock ticker</label><div className="request-input"><Icon name="search" size={19} /><input id="ticker" value={ticker} onChange={(event) => setTicker(event.target.value)} placeholder="Stock ticker, e.g. XYZ.L" pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,23}" required maxLength={24} autoComplete="off" /><button className="button" disabled={pending}>{pending ? "Creating…" : "Request research"}<Icon name="plus" size={16} /></button></div>{error && <p className="form-error" role="alert">{error}</p>}{created && <p className="form-success" role="status">Research queued. <Link href={`/research/${created.id}`}>Follow {created.ticker} →</Link></p>}</form></section>;
}

function MandateEditor() {
  const [mandate, setMandate] = useState<Mandate>({ ...DEFAULT_MANDATE });
  const [saved, setSaved] = useState(false);
  useEffect(() => { const timer = setTimeout(() => setMandate(loadMandate()), 0); return () => clearTimeout(timer); }, []);
  function save(event: FormEvent) { event.preventDefault(); localStorage.setItem("money.mandate", JSON.stringify(mandate)); setSaved(true); }
  return <div className="content-grid"><section className="panel form-panel"><SectionHeading kicker="RESEARCH CONSTRAINTS" title="Your research mandate" /><form onSubmit={save}><div className="field-grid"><div><label htmlFor="capital">Maximum assumed capital (£)</label><input id="capital" type="number" min="1" max="200" step="0.01" value={mandate.maximum_capital_gbp} onChange={(e) => { setSaved(false); setMandate({ ...mandate, maximum_capital_gbp: e.target.value }); }} /><small>Never more than £200. No portfolio access.</small></div><div><label htmlFor="minimum">Minimum horizon (days)</label><input id="minimum" type="number" min="1" max={mandate.maximum_horizon_days} value={mandate.minimum_horizon_days} onChange={(e) => { setSaved(false); setMandate({ ...mandate, minimum_horizon_days: Number(e.target.value) }); }} /></div><div><label htmlFor="maximum">Maximum horizon (days)</label><input id="maximum" type="number" min={mandate.minimum_horizon_days} max="30" value={mandate.maximum_horizon_days} onChange={(e) => { setSaved(false); setMandate({ ...mandate, maximum_horizon_days: Number(e.target.value) }); }} /></div></div><div className="fixed-policy"><span className="type-label fact">FACT / MANDATE</span><h3>Eligibility and ethical boundaries</h3><p>Trading 212 Stocks & Shares ISA · Individual stocks · GBP or GBX</p><div className="chips">{DEFAULT_MANDATE.excluded_activities.map((activity) => <span key={activity}>{humanize(activity)}</span>)}</div></div><div className="notice"><strong>£1,000 stretch objective</strong><span>Aspirational only. It cannot increase capital, weaken evidence standards or loosen risk limits.</span></div><button className="button" type="submit">Save mandate draft <Icon name="check" size={16} /></button>{saved && <span className="saved-message" role="status">Draft saved in this browser</span>}<p className="small-print">This browser draft is applied to new research requests. Each job stores its own immutable mandate version in the research database.</p></form></section><section className="panel side-note"><Icon name="lock" size={24} /><h2>Boundaries are part of the research.</h2><p>Unknown eligibility, prohibited business activities, stale critical evidence or a currency outside the mandate stop a candidate from progressing.</p><p>Enthusiasm cannot override these gates.</p></section></div>;
}

function ResearchDetail({ id, revision }: { id: string | undefined; revision: number }) {
  const validId = id && /^[a-f0-9-]{36}$/i.test(id);
  const job = useResource<Job>(validId ? `/api/research/${id}` : null, revision, true);
  const reports = useResource<Reports>(validId ? `/api/research/${id}/reports` : null, revision, true);
  const evidence = useResource<Evidence>(validId ? `/api/research/${id}/evidence` : null, revision, true);
  const [tab, setTab] = useState("firms");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  if (!validId) return <EmptyState title="A valid research ID is required" detail="Open a research record from the research jobs page." />;
  if (job.error) return <div className="notice error" role="alert">{job.error}</div>;
  if (!job.data) return <div className="notice" role="status">Loading the durable research record…</div>;
  const record = job.data;
  const packet = record.packet ?? reports.data?.packet;
  const packetSignal = packet?.signal && typeof packet.signal === "object" ? packet.signal as RecordData : null;
  const currentState = packetSignal ? signalState(packetSignal, now) : String(record.final_state ?? packet?.final_state ?? "INSUFFICIENT_EVIDENCE");
  const artifacts = reports.data?.artifacts ?? {};
  const locked = reports.data?.locked === true;
  return <>
    <section className="research-header panel"><div className="research-title"><div className="ticker-mark large">{record.ticker.slice(0, 2)}</div><div><p className="eyebrow">CANDIDATE RESEARCH</p><h2>{record.ticker}</h2><code>{record.id}</code></div></div><Badge value={record.status} /></section>
    {packet?.runtime === "demo" && <div className="notice demo"><strong>Synthetic demonstration record</strong>These fixture reports demonstrate the workflow. The upstream firms did not conduct live research, and this packet cannot publish an investment signal.</div>}
    {record.error_message && <div className="notice error" role="alert"><strong>{humanize(record.error_code ?? "Research stopped")}</strong>{record.error_message}</div>}
    <div className="stage-track" aria-label="Research progress">{["QUEUED", "SNAPSHOT_BUILD", "FIRST_PASS_RESEARCH", "FIRST_PASS_LOCKED", "LEAN_VALIDATION", "CREWAI_AUDIT", "COMPLETE"].map((stage, i) => <div className={JOB_STAGES.indexOf(record.status as typeof JOB_STAGES[number]) >= JOB_STAGES.indexOf(stage as typeof JOB_STAGES[number]) ? "reached" : ""} key={stage}><span>{i + 1}</span><small>{humanize(stage)}</small></div>)}</div>
    <div className="tabs" role="tablist" aria-label="Research sections">{[["firms", "Independent firms"], ["audit", "CIO & Red Team"], ["evidence", "Evidence & sources"], ["decision", "Money research state"]].map(([key, label]) => <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>)}</div>
    <section role="tabpanel">
      {tab === "firms" && <><div className={`barrier ${locked ? "locked" : ""}`}><Icon name="lock" size={20} /><div><strong>{locked ? "First-pass reports are locked" : "Independent research is sealed"}</strong><p>{locked ? "The three original reports are immutable. Downstream validation and audit can now inspect them together." : "No firm's conclusions are revealed to another firm during first-pass research. Reports unlock only when all three are persisted."}</p></div><Badge value={locked ? "FIRST_PASS_LOCKED" : "SEALED"} /></div><div className="firm-grid">{[["tradingagents", "TradingAgents", "A", "Technical, fundamental and market research"], ["ai_hedge_fund", "ai-hedge-fund", "B", "Independent investment philosophies"], ["qlib", "Qlib", "C", "Quantitative models and factor evidence"]].map(([key, name, letter, description]) => <FirmReportCard key={key} name={name} letter={letter} description={description} locked={locked} report={reports.data?.reports[key]} />)}</div><section className="panel validation-panel"><span className="type-label validation">VALIDATION RESULT</span><SectionHeading title="LEAN · Independent validation laboratory" />{artifacts.lean ? <DataValue value={artifacts.lean} /> : <p className="muted">Historical falsification begins after all first-pass reports are locked. LEAN is a validation laboratory, not another directional voter.</p>}</section></>}
      {tab === "audit" && <div className="content-grid"><section className="panel"><span className="type-label audit">AUDIT FINDING</span><SectionHeading title="CrewAI · Chief Investment Office" />{artifacts.audit ? <DataValue value={artifacts.audit} /> : <EmptyState title="Audit has not been published" detail="Auditors independently verify claims once first-pass reports are locked and validation evidence is available." icon="search" />}</section><section className="panel"><span className="type-label audit">ADVERSARIAL REVIEW</span><SectionHeading title="Red Team" />{artifacts.red_team ? <DataValue value={artifacts.red_team} /> : <EmptyState title="Challenge pending" detail="The Red Team looks for failure modes, unsupported claims and shared-source groupthink." icon="lock" />}</section><section className="panel"><span className="type-label inference">INFERENCE</span><SectionHeading title="Evidence independence" />{artifacts.independence ? <DataValue value={artifacts.independence} /> : <p className="muted">Source overlap is evaluated across reports. Three firms citing one article count as one underlying source.</p>}</section></div>}
      {tab === "evidence" && <section className="panel"><span className="type-label fact">FACT</span><SectionHeading title="Research snapshot & evidence" />{evidence.error ? <p role="alert">{evidence.error}</p> : evidence.data?.evidence.length ? <><p className="muted">Snapshot <code>{record.snapshot_id}</code> · Evidence retains provenance, timestamps and hashes.</p>{evidence.data.evidence.map((item, i) => <details className="evidence-item" key={String(item.evidence_id ?? i)}><summary>{String(item.kind ?? item.category ?? "Evidence")} <span>{String(item.source ?? item.provider ?? `Record ${i + 1}`)}</span></summary><DataRecord data={item} /></details>)}{evidence.data.snapshot && <details className="evidence-item"><summary>Full immutable snapshot</summary><DataRecord data={evidence.data.snapshot} /></details>}</> : <EmptyState title="No snapshot has been published" detail="Money freezes eligible, point-in-time objective evidence before research begins." />}</section>}
      {tab === "decision" && <section className="panel"><span className="type-label final-state">FINAL MONEY RESEARCH STATE</span><SectionHeading title="The decision packet" />{packet ? <><Badge value={currentState} />{currentState === "EXPIRED" && <div className="notice">This research setup has expired or been invalidated. It is no longer active.</div>}<p className="small-print">The immutable packet below is the historical record as issued. Its original state and levels are retained for audit and outcome evaluation.</p><DataRecord data={packet} /></> : <EmptyState title="No final research state yet" detail="Evidence, validation, audit and deterministic quality gates must all be considered. A completed review may still produce no research signal." />}</section>}
      {reports.error && <div className="notice error">{reports.error}</div>}
    </section>
  </>;
}

function Discovery({ revision }: { revision: number }) {
  const resource = useResource<{ candidates?: RecordData[]; discovery?: RecordData[] }>("/api/research/discovery", revision);
  const items = resource.data?.candidates ?? resource.data?.discovery ?? [];
  return <><div className="discovery-grid">{[["TA-Lib", "Technical structure", "Trend, momentum, volatility and volume."], ["Qlib", "Quantitative evidence", "Model rankings, factors and regimes."], ["Catalysts", "A reason for change", "Announcements, filings and company news."], ["Fundamentals", "Business evolution", "Financial statements and material changes."]].map(([name, label, detail], i) => <section className="panel discovery-card" key={name}><span className="channel-number">0{i + 1}</span><p className="eyebrow">{name}</p><h3>{label}</h3><p>{detail}</p><span className="channel-caption">Independent discovery channel</span></section>)}</div><div className="union-label">Four channels → union + deduplicate → one research shortlist</div><section className="panel"><SectionHeading title="Candidate shortlist" />{resource.error && <div className="notice error">{resource.error}</div>}{items.length ? items.map((item, i) => <div className="evidence-item" key={i}><DataRecord data={item} /></div>) : <EmptyState title="The shortlist is waiting for evidence" detail="Candidates appear when eligible instruments have a recorded discovery reason. A technical scanner never decides the final research state." icon="search" />}</section></>;
}

function Signals({ expired, revision }: { expired: boolean; revision: number }) {
  const resource = useResource<{ signals: RecordData[] }>(`/api/research/signals?expired=${expired}`, revision, true);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const signals = (resource.data?.signals ?? []).map(unwrapSignal).filter((signal) => (signalState(signal, now) === "EXPIRED") === expired);
  return <section className="panel"><SectionHeading title={expired ? "Expired research archive" : "Active research signals"} action={<span className="type-label final-state">MONEY RESEARCH STATE</span>} /><p className="muted">{expired ? "Expired setups remain available as historical evidence and for outcome evaluation." : "Every setup has an expiry time and invalidation conditions. A research candidate is an invitation to investigate."}</p>{resource.error && <div className="notice error">{resource.error}</div>}{signals.length ? <div className="signal-grid">{signals.map((signal, i) => <article className="signal-card" key={String(signal.research_id ?? i)}><Badge value={signalState(signal, now)} /><h3>{String(signal.ticker ?? signal.company ?? "Research signal")}</h3><DataRecord data={signal} />{typeof signal.research_id === "string" && <Link className="text-button" href={`/research/${signal.research_id}`}>Open research <Icon name="arrow" size={15} /></Link>}</article>)}</div> : <EmptyState title={expired ? "No expired setups recorded" : "No active research signals"} detail={expired ? "Research moves here when its validity window ends. It is never presented as an active setup after expiry." : "A signal appears only when the evidence supports it. Missing validation, unresolved material disagreement or a failed hard gate prevents publication."} icon={expired ? "clock" : "signal"} />}</section>;
}

function Outcomes({ revision }: { revision: number }) {
  const resource = useResource<{ outcomes: RecordData[] }>("/api/research/outcomes", revision);
  const outcomes = resource.data?.outcomes ?? [];
  return <><div className="outcome-horizons">{[1, 3, 5, 10, 30].map((day) => <div key={day}><strong>{day}<small>D</small></strong><span>Forward return</span></div>)}</div><section className="panel"><SectionHeading kicker="THE FEEDBACK LOOP" title="Research outcomes" /><p className="muted">Forward returns, MFE, MAE, target occurrence and invalidation are measured from published research. Manually recorded trades belong in a separate ledger.</p>{resource.error && <div className="notice error">{resource.error}</div>}{outcomes.length ? outcomes.map((outcome, i) => <div className="evidence-item" key={i}><DataRecord data={outcome} /></div>) : <EmptyState title="Performance starts with a track record" detail="No measured outcomes are available yet. Component reliability and discovery-channel contribution will be evaluated from observed results." icon="chart" />}</section><div className="notice"><strong>Measured contribution, not simulated certainty.</strong>Research firms, validation, audit, Red Team and evidence families are evaluated as the outcome ledger develops.</div></>;
}

function HealthView({ health, error, onRefresh }: { health: Health | null; error: string | null; onRefresh: () => void }) {
  return <section className="panel"><SectionHeading title="Service status" action={<button className="text-button" onClick={onRefresh}>Refresh <Icon name="arrow" size={15} /></button>} />{error && <div className="notice error" role="alert">{error}</div>}{health ? <><div className="health-summary"><Badge value={health.status} /><span>{health.mode} environment</span></div><div className="health-grid">{[["Database", health.database], ["Research worker", health.worker]].map(([name, value]) => <section className="health-card" key={String(name)}><Icon name={name === "Database" ? "layers" : "pulse"} size={22} /><h3>{String(name)}</h3><DataValue value={value} /></section>)}</div></> : <EmptyState title="Service status is unavailable" detail="Connection failures do not erase saved research. Check the research service and retry." icon="pulse" />}<div className="architecture-note"><span>Web application</span><Icon name="arrow" size={17} /><span>Durable job database</span><Icon name="arrow" size={17} /><span>Research compute</span></div><p className="small-print">The web application submits and retrieves jobs. Long-running research continues independently of browser sessions and request lifetimes.</p></section>;
}
