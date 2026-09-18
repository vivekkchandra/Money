"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { DEFAULT_MANDATE, humanize, isTerminal, JOB_STAGES, signalState, unwrapSignal, validateJobInput, type Evidence, type Health, type Job, type Mandate, type RecordData, type Reports } from "@/lib/contracts";
import { api } from "@/lib/client";
import { type Instrument } from "@/lib/instruments";
import { InstrumentPicker } from "./instrument-picker";
import { PersonalResearchDetail, PersonalRndNotice } from "./personal-rnd";
import { canonicalView } from "@/lib/routes";
import { type SystemMetrics } from "@/lib/system";
import { OperationalMetrics } from "./operational-metrics";
import { CrossExamination } from "./cross-examination";
import { ObjectiveDashboardCard, ObjectivePage } from "./objective";
import { UniverseRecords } from "./universe";
import { type UniversePage } from "@/lib/universe";
import { Billing, CustomerToolbar, CustomerWatchlist, InternalAdmin, Notifications, ResearchHistory, TeamSettings, UsageSummary } from "./customer-tools";
import { Badge, DataRecord, DataValue, EmptyState, FirmReportCard, Icon, JobList, SectionHeading } from "./primitives";

const NAV = [
  ["objective", "£200 / 30-Day Objective", "chart"],
  ["", "Overview", "grid"], ["mandate", "Research mandate", "sliders"], ["universe", "Universe & eligibility", "grid"], ["discovery", "Candidate discovery", "search"], ["jobs", "Research jobs", "layers"],
  ["signals", "Research signals", "signal"], ["watch", "Watch list", "clock"], ["rejected", "Rejected & insufficient", "lock"], ["expired", "Expired research", "clock"], ["evidence", "Evidence explorer", "document"], ["outcomes", "Outcomes", "chart"], ["performance", "Performance & calibration", "chart"], ["health", "System health", "pulse"], ["settings", "Settings", "sliders"],
];
const CUSTOMER_NAV = [["", "Overview", "grid"], ["objective", "£200 / 30-Day Objective", "chart"], ["jobs", "Research", "layers"], ["history", "Research history", "search"], ["watch", "Watchlist", "clock"], ["signals", "Research signals", "signal"], ["evidence", "Evidence explorer", "document"], ["outcomes", "Outcomes", "chart"], ["notifications", "Notifications", "clock"], ["mandate", "Research preferences", "sliders"], ["billing", "Plan & billing", "sliders"], ["team", "Team", "grid"], ["account", "Account", "lock"]];
const TITLES: Record<string, [string, string]> = {
  objective: ["Ambition, held to the evidence.", "£200 / 30-Day Objective"],
  "": ["A clearer view. An independent perspective.", "Your research workspace"],
  mandate: ["Define the boundaries.", "Research mandate"],
  discovery: ["More than one way to find an idea.", "Candidate discovery"],
  jobs: ["Follow the evidence as it develops.", "Research jobs"],
  signals: ["Research worth a closer look.", "Research signals"],
  expired: ["A record, not an active setup.", "Expired research"],
  outcomes: ["Measure what the research got right.", "Outcome intelligence"],
  health: ["A clear view of the system.", "System health"],
  research: ["Independent work. Open to scrutiny.", "Candidate research"],
  universe: ["Eligibility before investigation.", "Universe & eligibility"],
  watch: ["Ideas that need more evidence.", "Watch list"],
  rejected: ["The reasons research stopped.", "Rejected & insufficient evidence"],
  evidence: ["Every conclusion has a source.", "Evidence explorer"],
  performance: ["Confidence must be earned.", "Performance & calibration"],
  settings: ["A workspace with clear boundaries.", "Settings"],
  billing: ["A plan for your research.", "Plan & billing"],
  history: ["Your research, ready when you return.", "Research history"],
  notifications: ["Stay informed.", "Notifications"],
  team: ["Independent research. Shared workspace.", "Team settings"],
  admin: ["Service operations.", "Internal administration"],
};

function useResource<T>(path: string | null, refresh = 0, poll = false) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedPath, setLoadedPath] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const result = await api<T>(path!, { signal: controller.signal });
        if (active) { setData(result); setError(null); setLoadedPath(path); }
      } catch (reason) {
        if (active && !controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "Unable to load research"); setLoadedPath(path); }
      } finally { if (active && poll) timer = setTimeout(() => void load(), 5000); }
    }
    void load();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [path, refresh, poll]);
  return { data: loadedPath === path ? data : null, error: loadedPath === path ? error : null, loading: !!path && loadedPath !== path };
}

export function Workspace({ view: requestedView, commercial = false, personalRnd = false }: { view: string[]; commercial?: boolean; personalRnd?: boolean }) {
  const view = canonicalView(requestedView);
  const page = view[0] ?? "";
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const [sessionNotice, setSessionNotice] = useState<string | null>(null);
  useEffect(() => {
    const expire = () => { setSessionNotice("Your session expired. Sign in again to continue your research."); refresh(); };
    window.addEventListener("money:session-expired", expire);
    return () => window.removeEventListener("money:session-expired", expire);
  }, [refresh]);
  const session = useResource<{ configured: boolean; authenticated: boolean }>("/api/session", revision);
  const authenticated = session.data?.authenticated === true;
  const jobsResource = useResource<{ jobs: Job[] }>(authenticated ? "/api/research" : null, revision, true);
  const health = useResource<Health>(authenticated ? "/api/health" : null, revision, true);
  const rnd = personalRnd || health.data?.mode === "live_rnd";
  const metrics = useResource<SystemMetrics>(authenticated && page === "health" ? "/api/research/system" : null, revision, true);
  const jobs = jobsResource.data?.jobs ?? [];
  const active = jobs.filter((job) => !isTerminal(job.status));
  const [title, eyebrow] = TITLES[page] ?? ["This page is outside the research universe.", "Page not found"];

  return <div className="app-shell" data-money-page={page || "dashboard"}>
    <a className="skip-link" href="#main">Skip to content</a>
    <aside className="sidebar">
      <Link href="/" className="brand" aria-label="Money home"><span className="brand-symbol">m<span>·</span></span><span>money<span className="brand-period">.</span></span></Link>
      <p className="sidebar-caption">INDEPENDENT BY DESIGN</p>
      <div className="workspace-label"><span className="workspace-avatar">P</span><div>{commercial ? "Research workspace" : "Personal workspace"}<small>{rnd ? "Personal public-data R&D" : "UK equity research"}</small></div><Icon name="lock" size={15} /></div>
      <p className="nav-label">WORKSPACE</p>
      <nav aria-label="Main navigation">{(commercial ? CUSTOMER_NAV : NAV).map(([path, label, icon], index) => <Link href={`/${path || "dashboard"}`} key={label} className={`nav-link ${page === path || (page === "research" && path === "jobs") ? "selected" : ""} ${index === 4 ? "nav-divider" : ""}`} aria-current={page === path ? "page" : undefined}><Icon name={icon} size={18} /><span>{label}</span>{path === "jobs" && active.length > 0 && <span className="nav-count">{active.length}</span>}</Link>)}</nav>
      <div className="sidebar-bottom"><div className="manual-note"><Icon name="lock" size={18} /><div><strong>Your decisions. Always.</strong><p>Evidence and perspective.<br />Every investment decision stays yours.</p></div></div><div className="sidebar-footer"><span className="status-dot" />Research workspace<span>v0.1</span></div></div>
    </aside>
    <div className="main-column">
      <header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>{eyebrow}</strong></div><div className="topbar-right"><span className="universe-pill">{rnd ? "R&D · source currency" : "UK · GBP / GBX"}</span>{authenticated && <button className="icon-button" aria-label="Sign out" onClick={async () => { try { await api("/api/session", { method: "DELETE" }); setSessionNotice(null); refresh(); } catch { setSessionNotice("Sign-out could not be confirmed. Please retry."); } }}><Icon name="logout" size={18} /></button>}<span className="profile-avatar">P</span></div></header>
      <main id="main">
        <div className="page-heading"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1></div><span className="research-only"><Icon name="lock" size={14} />Research only</span></div>
        {sessionNotice && <div className="notice" role="status">{sessionNotice}</div>}
        {session.loading ? <div className="notice" role="status">Opening your workspace…</div> : !authenticated ? commercial ? <section className="panel"><h2>Welcome to your research workspace.</h2><p>{session.error ?? "Sign in to continue your investigations and inspect your saved research."}</p><Link className="button" href="/login">Sign in</Link> <Link className="text-button" href="/signup">Create an account</Link></section> : <SignIn configured={session.data?.configured === true} error={session.error} onSuccess={() => { setSessionNotice(null); refresh(); }} /> : <>
          {commercial && <CustomerToolbar />}
          {jobsResource.error && <div className="notice error" role="alert">{jobsResource.error}<button onClick={refresh}>Retry</button></div>}
          {health.data?.mode === "demo" && <div className="notice demo"><strong>Demonstration environment</strong> Research uses synthetic fixtures. Use DEMO.L to explore the workflow. Reports are not live firm research or investment opportunities.</div>}
          {rnd && !personalRnd && <PersonalRndNotice />}
          {page === "" && <>{commercial && <UsageSummary />}{jobsResource.loading ? <div className="notice" role="status">Loading saved investigations…</div> : <Dashboard jobs={jobs} active={active} health={health.data} onCreated={refresh} commercial={commercial} />}</>}
          {page === "mandate" && <MandateEditor commercial={commercial} />}
          {page === "objective" && <ObjectivePage />}
          {page === "jobs" && <><ResearchRequest onCreated={refresh} commercial={commercial} /><section className="panel"><SectionHeading title="Research activity" action={<button className="text-button" onClick={refresh}>Refresh <Icon name="arrow" size={15} /></button>} /><JobList jobs={jobs} /></section></>}
          {page === "research" && <ResearchDetail key={`${view[1]}-${view[2]}`} id={view[1]} section={view[2]} revision={revision} onRefresh={refresh} />}
          {page === "universe" && <Universe revision={revision} onRefresh={refresh} />}
          {(page === "watch" || page === "rejected") && (commercial && page === "watch" ? <CustomerWatchlist /> : <ResearchCollection jobs={jobs} kind={page} loading={jobsResource.loading} />)}
          {commercial && page === "history" && <ResearchHistory />}
          {commercial && page === "billing" && <Billing />}
          {commercial && page === "notifications" && <Notifications />}
          {commercial && page === "team" && <TeamSettings />}
          {commercial && page === "admin" && <InternalAdmin />}
          {page === "evidence" && <EvidenceExplorer jobs={jobs} loading={jobsResource.loading} />}
          {page === "discovery" && <Discovery revision={revision} />}
          {(page === "signals" || page === "expired") && <Signals expired={page === "expired"} revision={revision} />}
          {page === "outcomes" && <Outcomes revision={revision} />}
          {page === "performance" && <Performance revision={revision} />}
          {page === "settings" && <Settings />}
          {page === "health" && <><HealthView health={health.data} error={health.error} onRefresh={refresh} /><OperationalMetrics metrics={metrics.data} error={metrics.error} loading={metrics.loading} onRefresh={refresh} /></>}
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
  return <div className="welcome-grid"><section className="welcome-hero"><p className="eyebrow">A RESEARCH COLLECTIVE</p><h2>More perspectives.<br />Better questions.</h2><p>Two independent firms investigate the same evidence, with optional Qlib quantitative research. Mandatory LEAN validation tests their hypotheses. A separate investment office challenges the conclusions.</p><div className="firm-tokens"><span>TradingAgents</span><span>ai-hedge-fund</span><span>Qlib (optional)</span></div><div className="welcome-line"><Icon name="lock" size={17} />Shared facts. Independent opinions.</div></section><section className="panel sign-in"><span className="empty-icon"><Icon name="lock" size={25} /></span><h2>Your private research workspace</h2><p className="muted">Sign in to review evidence, follow research and investigate new ideas.</p>{configured ? <form onSubmit={submit}><label htmlFor="password">Workspace password</label><input id="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required maxLength={512} /><button className="button full-width" disabled={pending}>{pending ? "Signing in…" : "Open workspace"}<Icon name="arrow" size={17} /></button></form> : <div className="notice">{error ?? "This workspace is awaiting secure configuration. Research access will become available once the workspace owner completes setup."}</div>}{message && <p className="form-error" role="alert">{message}</p>}<p className="small-print">Trading 212 provides an eligibility constraint only. Money never accesses your portfolio or executes trades.</p></section></div>;
}

function Dashboard({ jobs, active, health, onCreated, commercial = false }: { jobs: Job[]; active: Job[]; health: Health | null; onCreated: () => void; commercial?: boolean }) {
  return <>
    <section className="overview-hero"><div><p className="eyebrow">YOUR RESEARCH, WITH PERSPECTIVE</p><h2>Conviction begins<br />with better evidence.</h2><p>Independent analysis. A rigorous challenge.<br />Space for your own judgement.</p><Link href="/jobs" className="button light">Start researching<Icon name="arrow" size={17} /></Link></div><div className="research-orbit" aria-label="Two independent firms and optional quantitative research feed a shared audit"><span className="orbit-center">m<span>·</span></span><span className="orbit-node node-one">TradingAgents<small>RESEARCH FIRM A</small></span><span className="orbit-node node-two">ai-hedge-fund<small>RESEARCH FIRM B</small></span><span className="orbit-node node-three">Qlib<small>OPTIONAL QUANT</small></span><span className="orbit-caption">INDEPENDENT PERSPECTIVES · SHARED FACTS</span></div></section>
    <div className="stats-grid"><Stat label="Active research" value={String(active.length).padStart(2, "0")} note="Independent investigations" icon="layers" /><Stat label="Completed reviews" value={String(jobs.filter((job) => job.status === "COMPLETE").length).padStart(2, "0")} note="Research records, not endorsements" icon="document" /><Stat label="Capital assumption" value="£200" note="Maximum · research only" icon="sliders" /><Stat label="Research horizon" value="1–30" note="Days · short-horizon evidence" icon="clock" /></div>
    <div className="content-grid"><section className="panel"><SectionHeading kicker="THE RESEARCH DESK" title="Recent investigations" action={<Link className="text-button" href="/jobs">View all <Icon name="arrow" size={15} /></Link>} /><JobList jobs={jobs.slice(0, 5)} /></section><section className="panel mandate-summary"><p className="eyebrow">YOUR NORTH STAR</p><h2>A focused mandate.</h2><div className="mandate-line"><span>Universe</span><strong>Trading 212 GBX stocks</strong></div><div className="mandate-line"><span>Instruments</span><strong>Individual stocks</strong></div><div className="mandate-line"><span>Quote currency</span><strong>GBX</strong></div><div className="ethical-note"><Icon name="check" size={17} /><span>Defence, weapons and oil activities excluded.</span></div><Link href="/mandate" className="text-button">Review mandate <Icon name="arrow" size={15} /></Link></section></div>
    <ResearchRequest onCreated={onCreated} commercial={commercial} />
    <ObjectiveDashboardCard />
    {!commercial && <Alerts />}
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
    if (stored) return validateJobInput({ ticker: "VALID", mandate: JSON.parse(stored) })?.mandate ?? { ...DEFAULT_MANDATE };
  } catch { /* A draft is optional; canonical validation runs on the server. */ }
  return { ...DEFAULT_MANDATE };
}

export function ResearchRequest({ onCreated, commercial = false }: { onCreated: () => void; commercial?: boolean }) {
  const [selected, setSelected] = useState<Instrument | null>(null);
  const [searchRevision, setSearchRevision] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Job | null>(null);
  const requestIdentity = useRef<{ body: string; key: string } | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || !selected?.research_allowed) return;
    setPending(true); setError(null); setCreated(null);
    try {
      const mandate = commercial ? (await api<{ mandate: Mandate }>("/api/product/preferences")).mandate : loadMandate();
      const body = JSON.stringify({ ticker: selected.ticker, mandate });
      if (requestIdentity.current?.body !== body) requestIdentity.current = { body, key: crypto.randomUUID() };
      const job = await api<Job>("/api/research", { method: "POST", headers: { "Idempotency-Key": requestIdentity.current.key }, body });
      requestIdentity.current = null; setCreated(job); setSelected(null); setSearchRevision(value => value + 1); onCreated();
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to create research"); }
    finally { setPending(false); }
  }
  return <section className="request-panel"><div><p className="eyebrow">FOLLOW YOUR CURIOSITY</p><h2>What are you researching?</h2><p>Find a company. The deployment’s research rules apply before an investigation starts.</p></div><form onSubmit={submit}><InstrumentPicker key={searchRevision} selected={selected} onSelect={setSelected} disabled={pending} /><button className="button request-submit" disabled={pending || !selected?.research_allowed}>{pending ? "Creating…" : "Request research"}<Icon name="plus" size={16} /></button>{error && <p className="form-error" role="alert">{error}</p>}{created && <p className="form-success" role="status">Research queued. <Link href={`/research/${created.id}`}>Follow {created.ticker} →</Link></p>}</form></section>;
}

function MandateEditor({ commercial = false }: { commercial?: boolean }) {
  const [mandate, setMandate] = useState<Mandate>({ ...DEFAULT_MANDATE });
  const [saved, setSaved] = useState(false);
  const [researchEmail, setResearchEmail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active = true; if (commercial) { api<{ mandate: Mandate; research_email: boolean }>("/api/product/preferences").then((result) => { if (active) { setMandate(result.mandate); setResearchEmail(result.research_email); } }).catch((reason) => { if (active) setError(reason.message); }); return () => { active = false; }; } const timer = setTimeout(() => setMandate(loadMandate()), 0); return () => clearTimeout(timer); }, [commercial]);
  async function save(event: FormEvent) { event.preventDefault(); setError(null); try { if (commercial) await api("/api/product/preferences", { method: "PUT", body: JSON.stringify({ mandate, research_email: researchEmail }) }); else localStorage.setItem("money.mandate", JSON.stringify(mandate)); setSaved(true); } catch (reason) { setError(reason instanceof Error ? reason.message : "Preferences could not be saved."); } }
return <div className="content-grid"><section className="panel form-panel"><SectionHeading kicker="RESEARCH CONSTRAINTS" title="Your research mandate" /><form onSubmit={save}><div className="field-grid"><div><label htmlFor="capital">Maximum assumed capital (£)</label><input id="capital" type="number" min="1" max="200" step="0.01" value={mandate.maximum_capital_gbp} onChange={(e) => { setSaved(false); setMandate({ ...mandate, maximum_capital_gbp: e.target.value }); }} /><small>Never more than £200. No portfolio access.</small></div><div><label htmlFor="minimum">Minimum horizon (days)</label><input id="minimum" type="number" min="1" max={mandate.maximum_horizon_days} value={mandate.minimum_horizon_days} onChange={(e) => { setSaved(false); setMandate({ ...mandate, minimum_horizon_days: Number(e.target.value) }); }} /></div><div><label htmlFor="maximum">Maximum horizon (days)</label><input id="maximum" type="number" min={mandate.minimum_horizon_days} max="30" value={mandate.maximum_horizon_days} onChange={(e) => { setSaved(false); setMandate({ ...mandate, maximum_horizon_days: Number(e.target.value) }); }} /></div></div><div className="fixed-policy"><span className="type-label fact">FACT / MANDATE</span><h3>Eligibility and ethical boundaries</h3><p>Trading 212 live accessible universe · Individual stocks · GBX</p><div className="chips">{DEFAULT_MANDATE.excluded_activities.map((activity) => <span key={activity}>{humanize(activity)}</span>)}</div></div><div className="notice"><strong>£1,000 stretch objective</strong><span>Aspirational only. It cannot increase capital, weaken evidence standards or loosen risk limits.</span></div>{commercial && <label className="checkbox-label"><input type="checkbox" checked={researchEmail} onChange={(event) => { setResearchEmail(event.target.checked); setSaved(false); }} /><span>Email workspace research updates</span></label>}<button className="button" type="submit">{commercial ? "Save workspace preferences" : "Save mandate draft"} <Icon name="check" size={16} /></button>{saved && <span className="saved-message" role="status">{commercial ? "Saved to your workspace" : "Draft saved in this browser"}</span>}{error && <p className="form-error" role="alert">{error}</p>}<p className="small-print">{commercial ? "Workspace preferences are stored securely and applied to new research requests." : "This browser draft is applied to new research requests."} Each job retains its own immutable mandate.</p></form></section><section className="panel side-note"><Icon name="lock" size={24} /><h2>Boundaries are part of the research.</h2><p>Unknown eligibility, prohibited business activities, stale critical evidence or a currency outside the mandate stop a candidate from progressing.</p><p>Enthusiasm cannot override these gates.</p></section></div>;
}

const RESEARCH_TABS = [["firms", "Independent firms"], ["audit", "CIO & Red Team"], ["cross-examination", "Cross-examination"], ["evidence", "Evidence & sources"], ["decision", "Money research state"]];

function objectRecord(value: unknown): RecordData | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as RecordData : null;
}

export function FirstPassResearchPanel({ reports, packet, snapshot, snapshotId, evidenceHref }: {
  reports: Reports | null; packet?: RecordData | null; snapshot?: RecordData | null;
  snapshotId?: string | null; evidenceHref: string;
}) {
  // Display checks mirror the server's mode binding; they do not verify hashes.
  const frozen = objectRecord(packet?.frozen_snapshot);
  const expectedId = packet?.snapshot_id ?? snapshotId;
  const boundSnapshot = [frozen, snapshot].find((candidate) => candidate
    && typeof expectedId === "string" && candidate.snapshot_id === expectedId
    && typeof candidate.hash === "string" && /^[a-f0-9]{64}$/.test(candidate.hash)
    && (!packet || candidate.hash === packet.snapshot_hash));
  const validMode = (value: unknown) => value === undefined || typeof value === "boolean";
  const mode = packet ? packet.qlib_enabled ?? true : boundSnapshot?.qlib_enabled ?? true;
  const inconsistentMode = !validMode(packet?.qlib_enabled) || !validMode(boundSnapshot?.qlib_enabled)
    || (packet && boundSnapshot && (packet.qlib_enabled ?? true) !== (boundSnapshot.qlib_enabled ?? true))
    || (packet?.qlib_enabled === false && boundSnapshot !== frozen)
    || (mode === false && (!boundSnapshot || boundSnapshot.qlib_enabled !== false));
  const disabled = !inconsistentMode && mode === false;
  const firms = [
    ["tradingagents", "TradingAgents", "A", "Technical, fundamental and market research"],
    ["ai_hedge_fund", "ai-hedge-fund", "B", "Independent investment philosophies"],
    ...(!disabled ? [["qlib", "Qlib", "C", "Optional quantitative models and factor evidence"]] : []),
  ];
  const recorded = reports?.reports ?? {};
  const identitiesMatch = Object.keys(recorded).length === firms.length
    && firms.every(([firm]) => recorded[firm] && (recorded[firm].firm === undefined || recorded[firm].firm === firm));
  const inconsistentReports = reports?.locked === true && !identitiesMatch;
  const locked = reports?.locked === true && identitiesMatch && !inconsistentMode;
  return <>
    {(inconsistentMode || inconsistentReports) && <div className="notice error" role="alert">First-pass configuration or report identities are inconsistent. Reports remain sealed; no qualification is inferred.</div>}
    <div className={`barrier ${locked ? "locked" : ""}`}><Icon name="lock" size={20} /><div><strong>{locked ? "First-pass reports are locked" : "Independent research is sealed"}</strong><p>{locked ? "The configured original reports are immutable. Downstream validation and audit can now inspect them together." : "No firm's conclusions are revealed to another firm during first-pass research. Reports unlock only when every configured required report is persisted."}</p></div><Badge value={locked ? "FIRST_PASS_LOCKED" : "SEALED"} /></div>
    {disabled && !inconsistentReports && <p className="notice">Qlib is disabled for this frozen snapshot: not run. No Qlib report or quantitative qualification is claimed. TradingAgents and AI-Hedge-Fund remain independent; LEAN validation remains mandatory.</p>}
    {!disabled && !packet && !boundSnapshot && <p className="small-print">Qlib configuration is not yet confirmed by snapshot provenance. A missing or loading report does not mean Qlib is disabled.</p>}
    <div className="firm-grid">{firms.map(([key, name, letter, description]) => <FirmReportCard key={key} name={name} letter={letter} description={description} locked={locked} report={recorded[key]} evidenceHref={evidenceHref} />)}</div>
    <section className="panel validation-panel"><span className="type-label validation">VALIDATION RESULT</span><SectionHeading title="LEAN · Mandatory independent validation laboratory" />{locked && reports?.artifacts.lean ? <DataValue value={reports.artifacts.lean} /> : <p className="muted">Historical falsification is mandatory and begins after all configured first-pass reports are locked. LEAN is a validation laboratory, not another directional voter. Missing validation is not a pass.</p>}</section>
  </>;
}

function ResearchDetail({ id, section, revision, onRefresh }: { id: string | undefined; section?: string; revision: number; onRefresh: () => void }) {
  const validId = id && /^[a-f0-9-]{36}$/i.test(id);
  const job = useResource<Job>(validId ? `/api/research/${id}` : null, revision, true);
  const personalRnd = job.data?.research_kind === "live_rnd" || job.data?.packet?.runtime === "live_rnd";
  const reports = useResource<Reports>(validId && job.data && !personalRnd ? `/api/research/${id}/reports` : null, revision, true);
  const evidence = useResource<Evidence>(validId ? `/api/research/${id}/evidence` : null, revision, true);
  const [tab, setTab] = useState(RESEARCH_TABS.some(([key]) => key === section) ? section! : "firms");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  if (!validId) return <EmptyState title="A valid research ID is required" detail="Open a research record from the research jobs page." />;
  if (job.error) return <div className="notice error" role="alert">{job.error}<button onClick={onRefresh}>Retry</button></div>;
  if (!job.data) return <div className="notice" role="status">Loading the durable research record…</div>;
  const record = job.data;
  if (personalRnd) return <PersonalResearchDetail job={record} evidence={evidence.data} evidenceError={evidence.error} onRefresh={onRefresh} />;
  const packet = record.packet ?? reports.data?.packet;
  const packetSignal = packet?.signal && typeof packet.signal === "object" ? packet.signal as RecordData : null;
  const currentState = packetSignal ? signalState(packetSignal, now) : String(record.final_state ?? packet?.final_state ?? "INSUFFICIENT_EVIDENCE");
  const artifacts = reports.data?.artifacts ?? {};
  return <>
    <section className="research-header panel"><div className="research-title"><div className="ticker-mark large">{record.ticker.slice(0, 2)}</div><div><p className="eyebrow">CANDIDATE RESEARCH</p><h2>{record.ticker}</h2><code>{record.id}</code></div></div><Badge value={record.status} /></section>
    {packet?.runtime === "demo" && <div className="notice demo"><strong>Synthetic demonstration record</strong>These fixture reports demonstrate the workflow. The upstream firms did not conduct live research, and this packet cannot publish an investment signal.</div>}
    {record.error_message && <div className="notice error" role="alert"><strong>{humanize(record.error_code ?? "Research stopped")}</strong>{record.error_message}</div>}
    <div className="stage-track" aria-label="Research progress">{["QUEUED", "SNAPSHOT_BUILD", "FIRST_PASS_RESEARCH", "FIRST_PASS_LOCKED", "LEAN_VALIDATION", "CREWAI_AUDIT", "COMPLETE"].map((stage, i) => <div className={JOB_STAGES.indexOf((record.current_stage || record.status) as typeof JOB_STAGES[number]) >= JOB_STAGES.indexOf(stage as typeof JOB_STAGES[number]) ? "reached" : ""} key={stage}><span>{i + 1}</span><small>{humanize(stage)}</small></div>)}</div>
    <div className="tabs" role="tablist" aria-label="Research sections">{RESEARCH_TABS.map(([key, label]) => <button key={key} id={`tab-${key}`} role="tab" aria-selected={tab === key} aria-controls="research-panel" tabIndex={tab === key ? 0 : -1} onClick={() => setTab(key)} onKeyDown={(event) => {
      const keys = RESEARCH_TABS.map(([key]) => key);
      const index = keys.indexOf(tab);
      const next = event.key === "ArrowRight" ? (index + 1) % keys.length : event.key === "ArrowLeft" ? (index + keys.length - 1) % keys.length : event.key === "Home" ? 0 : event.key === "End" ? keys.length - 1 : -1;
      if (next >= 0) { event.preventDefault(); setTab(keys[next]); document.getElementById(`tab-${keys[next]}`)?.focus(); }
    }}>{label}</button>)}</div>
    <section id="research-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0}>
      {tab === "cross-examination" && <CrossExamination artifact={artifacts.cross_examination} evidenceHref={`/research/${id}/evidence`} />}
      {tab === "firms" && <FirstPassResearchPanel reports={reports.data} packet={packet} snapshot={evidence.data?.snapshot} snapshotId={record.snapshot_id} evidenceHref={`/research/${id}/evidence`} />}
      {tab === "audit" && <div className="content-grid"><section className="panel"><span className="type-label audit">AUDIT FINDING</span><SectionHeading title="CrewAI · Chief Investment Office" />{artifacts.audit ? <DataValue value={artifacts.audit} evidenceHref={`/research/${id}/evidence`} /> : <EmptyState title="Audit has not been published" detail="Auditors independently verify claims once first-pass reports are locked and validation evidence is available." icon="search" />}</section><section className="panel"><span className="type-label audit">RED TEAM FINDING</span><SectionHeading title="Red Team" />{artifacts.red_team ? <DataValue value={artifacts.red_team} evidenceHref={`/research/${id}/evidence`} /> : <EmptyState title="Challenge pending" detail="The Red Team looks for failure modes, unsupported claims and shared-source groupthink." icon="lock" />}</section><section className="panel"><span className="type-label inference">INFERENCE</span><SectionHeading title="Evidence independence" />{artifacts.independence ? <DataValue value={artifacts.independence} /> : <p className="muted">Source overlap is evaluated across reports. Multiple firms citing one article count as one underlying source.</p>}</section></div>}
      {tab === "evidence" && <section className="panel"><span className="type-label fact">FACT</span><SectionHeading title="Research snapshot & evidence" />{evidence.error ? <p role="alert">{evidence.error}</p> : evidence.data?.evidence.length ? <><p className="muted">Snapshot <code>{record.snapshot_id}</code> · Evidence retains provenance, timestamps and hashes.</p>{evidence.data.evidence.map((item, i) => <details open className="evidence-item" id={`evidence-${encodeURIComponent(String(item.evidence_id ?? i))}`} key={String(item.evidence_id ?? i)}><summary>{String(item.kind ?? item.category ?? "Evidence")} <span>{String(item.source ?? item.provider ?? `Record ${i + 1}`)}</span></summary><DataRecord data={item} /></details>)}{evidence.data.snapshot && <details className="evidence-item"><summary>Full immutable snapshot</summary><DataRecord data={evidence.data.snapshot} /></details>}</> : <EmptyState title="No snapshot has been published" detail="Money freezes eligible, point-in-time objective evidence before research begins." />}</section>}
      {tab === "decision" && <section className="panel"><span className="type-label final-state">FINAL MONEY RESEARCH STATE</span><SectionHeading title="The decision packet" />{packet ? <><Badge value={currentState} />{currentState === "EXPIRED" && <div className="notice">This research setup has expired or been invalidated. It is no longer active.</div>}<p className="small-print">The immutable packet below is the historical record as issued. Its original state and levels are retained for audit and outcome evaluation.</p><DataRecord data={packet} /></> : <EmptyState title="No final research state yet" detail="Evidence, validation, audit and deterministic quality gates must all be considered. A completed review may still produce no research signal." />}</section>}
      {reports.error && <div className="notice error">{reports.error}</div>}
    </section>
  </>;
}

function Discovery({ revision }: { revision: number }) {
  const resource = useResource<{ candidates?: RecordData[]; discovery?: RecordData[] }>("/api/research/discovery", revision);
  const items = resource.data?.candidates ?? resource.data?.discovery ?? [];
  return <><div className="discovery-grid">{[["TA-Lib", "Technical structure", "Trend, momentum, volatility and volume."], ["Qlib · optional", "Quantitative evidence", "Model rankings, factors and regimes only when enabled and qualified."], ["Catalysts", "A reason for change", "Announcements, filings and company news."], ["Fundamentals", "Business evolution", "Financial statements and material changes."]].map(([name, label, detail], i) => <section className="panel discovery-card" key={name}><span className="channel-number">0{i + 1}</span><p className="eyebrow">{name}</p><h3>{label}</h3><p>{detail}</p><span className="channel-caption">Independent discovery channel</span></section>)}</div><div className="union-label">Enabled channels → union + deduplicate → one research shortlist</div><section className="panel"><SectionHeading title="Candidate shortlist" />{resource.error && <div className="notice error">{resource.error}</div>}{items.length ? items.map((item, i) => <div className="evidence-item" key={i}><DataRecord data={item} /></div>) : <EmptyState title="The shortlist is waiting for evidence" detail="Candidates appear when eligible instruments have a recorded discovery reason. A technical scanner never decides the final research state." icon="search" />}</section></>;
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
  return <section className="panel"><SectionHeading title="Service status" action={<button className="text-button" onClick={onRefresh}>Refresh <Icon name="arrow" size={15} /></button>} />{error && <div className="notice error" role="alert">{error}</div>}{health ? <><div className="health-summary"><Badge value={health.status} /><span>{health.environment ?? "Unknown environment"} · {health.mode} research</span></div><div className="health-grid">{[["Web", "READY"], ["API", health.status], ["Database", health.database], ["Research worker", health.worker]].map(([name, value]) => <section className="health-card" key={String(name)}><Icon name={name === "Database" ? "layers" : "pulse"} size={22} /><h3>{String(name)}</h3><DataValue value={value} /></section>)}</div><DataRecord data={{ money_version: health.version, git_commit: health.git_sha, schema_revision: health.schema_revision, queue_depth: health.queue_depth, oldest_queue_age_seconds: health.queue_age_seconds }} /><details><summary>Research integration qualification</summary><p className="small-print">API availability does not certify production research. Qualification is recorded in each immutable research packet.</p><div className="qualification-list">{["Live stock eligibility", "Market data", "Filings", "News & catalysts", "TradingAgents", "ai-hedge-fund", "Qlib (optional)", "LEAN (mandatory)", "CrewAI"].map((name) => <div key={name}><span>{name}</span><Badge value="UNKNOWN" label="Not verified by this health check" /></div>)}</div></details></> : <EmptyState title="Service status is unavailable" detail="Connection failures do not erase saved research. Check the research service and retry." icon="pulse" />}<div className="architecture-note"><span>Web application</span><Icon name="arrow" size={17} /><span>Durable job database</span><Icon name="arrow" size={17} /><span>Research compute</span></div><p className="small-print">The web application submits and retrieves jobs. Long-running research continues independently of browser sessions and request lifetimes.</p></section>;
}

function Universe({ revision, onRefresh }: { revision: number; onRefresh: () => void }) {
  const [offset, setOffset] = useState(0), [now, setNow] = useState(() => Date.now());
  const resource = useResource<UniversePage>(`/api/research/universe?limit=20&offset=${offset}`, revision, true);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const live = resource.data?.coverage === "reviewed_manifest" ? resource.data : null;
  return <section className="panel"><SectionHeading kicker="TRADING 212 · LIVE GBX STOCKS" title="Reviewed instruments" action={<button className="text-button" onClick={onRefresh}>Refresh</button>} />{resource.loading ? <div className="notice" role="status">Loading eligibility records…</div> : resource.error ? <div className="notice error" role="alert">{resource.error}<button onClick={onRefresh}>Retry</button></div> : resource.data && <UniverseRecords page={resource.data} now={now} />}{live && <nav className="objective-pagination" aria-label="Reviewed universe pages"><button className="button secondary" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>Previous instruments</button><span>Page {Math.floor(offset / 20) + 1}</span><button className="button secondary" disabled={!!resource.error || offset + 20 >= live.total || offset >= 10000} onClick={() => setOffset(value => value + 20)}>Next instruments</button></nav>}<div className="notice"><strong>100 GBX = £1 GBP.</strong>Original quote currency and normalized research values remain visible in the evidence record.</div></section>;
}

function ResearchCollection({ jobs, kind, loading }: { jobs: Job[]; kind: "watch" | "rejected"; loading: boolean }) {
  const filtered = jobs.filter((job) => kind === "watch" ? job.final_state === "WATCH" : ["REJECT", "INSUFFICIENT_EVIDENCE"].includes(job.final_state ?? "") || ["REJECTED", "FAILED"].includes(job.status));
  return <section className="panel"><SectionHeading title={kind === "watch" ? "Research watch list" : "Rejected & insufficient evidence"} /><p className="muted">{kind === "watch" ? "Candidates assigned WATCH by the evidence review. Watching a candidate does not imply an investment position." : "A failed gate or missing evidence is a meaningful result. Open a record to inspect the recorded reasons and provenance."}</p>{loading ? <div className="notice" role="status">Loading research states…</div> : filtered.length ? <JobList jobs={filtered} /> : <EmptyState title={kind === "watch" ? "No watch candidates recorded" : "No stopped research recorded"} detail="This view reflects saved research states in the most recent 50 jobs. No results are inferred from firm votes." />}</section>;
}

function EvidenceExplorer({ jobs, loading }: { jobs: Job[]; loading: boolean }) {
  const [query, setQuery] = useState("");
  const records = jobs.filter((job) => job.snapshot_id && `${job.ticker} ${job.id}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="panel"><SectionHeading kicker="FACTS · PROVENANCE · POINT IN TIME" title="Evidence explorer" /><p className="muted">Open a frozen snapshot to inspect its evidence IDs, original sources, publication and retrieval times, provider status and content hashes.</p><label htmlFor="evidence-search">Find a researched ticker or research ID</label><input id="evidence-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search the most recent 50 jobs" />{loading ? <div className="notice" role="status">Loading evidence index…</div> : records.length ? records.map((job) => <Link className="job-row" key={job.id} href={`/research/${job.id}/evidence`}><Icon name="document" /><div className="job-name"><strong>{job.ticker}</strong><span>{job.snapshot_id}</span></div><span className="type-label fact">FROZEN SNAPSHOT</span><Icon name="arrow" /></Link>) : <EmptyState title="No matching snapshots" detail="Evidence becomes available after a candidate passes eligibility and its point-in-time snapshot has been persisted." />}</section>;
}

function Performance({ revision }: { revision: number }) {
  const resource = useResource<{ outcomes: RecordData[] }>("/api/research/outcomes", revision);
  return <section className="panel"><SectionHeading title="Performance & calibration" action={<Badge value="UNCALIBRATED" />} /><p className="muted">Confidence requires observed out-of-sample outcomes, a defined method and an adequate sample. The number of research records alone cannot establish reliability.</p>{resource.loading ? <div className="notice" role="status">Loading the outcome ledger…</div> : resource.error ? <div className="notice error" role="alert">{resource.error}</div> : <DataRecord data={{ recorded_outcomes: resource.data?.outcomes.length ?? 0, calibrated_confidence: "Not established", calibration_method: "No approved calibration record", strong_research_candidate: "Unavailable until empirical reliability requirements are met" }} />}<Link className="text-button" href="/outcomes">Inspect measured research outcomes →</Link><div className="notice">Research outcomes describe hypothetical published experiments. They do not assert that you entered a position.</div></section>;
}

function Settings() {
  const [message, setMessage] = useState<string | null>(null);
  return <div className="content-grid"><section className="panel"><SectionHeading title="Private workspace" /><DataRecord data={{ access_mode: "Private single-user workspace", session_lifetime: "8 hours; server-validated on every research request", privacy: "No account balances, broker positions or actual capital are collected", research_delivery: "Available in this web workspace", email_alerts: "Not configured", telegram_alerts: "Not configured" }} /><Link className="text-button" href="/mandate">Review research mandate →</Link></section><section className="panel"><SectionHeading title="Browser preferences" /><p className="muted">Your mandate draft is saved on this browser and attached to each new research request. Saved research remains in the research database.</p><button className="button" onClick={() => { try { localStorage.removeItem("money.mandate"); setMessage("Browser mandate draft reset to the £200 default."); } catch { setMessage("Browser storage is unavailable."); } }}>Reset browser mandate draft</button>{message && <p className="small-print" role="status">{message}</p>}<p className="small-print">Passwords, sessions and provider credentials are never placed in browser storage.</p></section></div>;
}

function Alerts() {
  const resource = useResource<{ alerts: RecordData[] }>("/api/research/alerts", 0, true);
  return <section className="panel"><SectionHeading kicker="INFORMATIONAL NOTIFICATIONS" title="Research updates" />{resource.loading ? <p role="status">Checking for research updates…</p> : resource.error ? <p className="form-error" role="alert">{resource.error}</p> : resource.data?.alerts.length ? resource.data.alerts.slice(0, 10).map((alert, index) => {
    const payload = alert.payload && typeof alert.payload === "object" ? alert.payload as RecordData : {};
    return <article className="evidence-item" key={String(alert.id ?? index)}><Badge value={String(payload.event ?? "RESEARCH_UPDATE")} /><p>{String(payload.message ?? "A research record has an update.")}</p>{typeof alert.job_id === "string" && /^[a-f0-9-]{36}$/i.test(alert.job_id) && <Link className="text-button" href={`/research/${alert.job_id}`}>Review research →</Link>}</article>;
  }) : <p className="muted">No research updates yet. Completed research and recorded state changes appear here.</p>}</section>;
}
