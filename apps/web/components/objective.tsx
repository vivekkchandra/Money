"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/client";
import { currentOpportunities, OBJECTIVE_ORDER, OBJECTIVE_PAGE_SIZE, observeObjective, type ObjectiveLoad, type ObjectiveRanking, type Opportunity } from "@/lib/objective";
import { EmptyState, Icon, SectionHeading } from "./primitives";

const pounds = (value: number) => new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
const date = (value: string) => new Date(value).toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC").replace(/Z$/, " UTC");

export function ObjectiveDashboardCard() {
  return <section className="panel objective-link"><div><p className="eyebrow">AN ASPIRATION, NOT A FORECAST</p><h2>£200 / 30-Day Objective</h2><p>Explore qualified research scenarios against a £1,000 profit aspiration. Evidence and risk limits always come first.</p></div><Link className="button" href="/objective">Explore the objective <Icon name="arrow" size={16} /></Link></section>;
}

export function ObjectivePage() {
  const [offset, setOffset] = useState(0), [revision, setRevision] = useState(0);
  const [state, setState] = useState<ObjectiveLoad>({ report: null, error: null, loading: true });
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => observeObjective(offset, (path, signal) => api(path, { signal }), setState), [offset, revision]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const report = state.report?.offset === offset ? state.report : null;
  const retry = () => setRevision(value => value + 1);
  return <>
    <section className="objective-hero panel" aria-labelledby="objective-heading"><div><span className="type-label validation">RESEARCH SIMULATION</span><h2 id="objective-heading">An ambitious question.<br />An unchanged standard of evidence.</h2><p>What might qualified research scenarios support over 30 days? The target is aspirational. It never changes eligibility, ethical exclusions, risk thresholds or evidence requirements.</p></div><dl className="objective-numbers"><div><dt>Starting assumption</dt><dd>£200</dd></div><div><dt>Aspirational profit</dt><dd>£1,000</dd></div><div><dt>Implied end value</dt><dd>£1,200</dd></div><div><dt>Required return</dt><dd>+500%</dd></div><div><dt>Research horizon</dt><dd>30 days</dd></div></dl></section>
    <div className="notice"><strong>Alternatives, not a portfolio</strong><span>No positions, actual returns, account balance or countdown are inferred. Never add these allocations together. Returns are not guaranteed; losses can exceed modelled downside.</span></div>
    <section className="panel"><SectionHeading kicker="SEALED LIVE RESEARCH ONLY" title="Qualified scenario comparison" action={<button className="text-button" onClick={retry}>Refresh <Icon name="arrow" size={15} /></button>} />
      <p className="muted">GBX individual stocks · verified live Trading 212 membership · ethical screening. Independent firms, LEAN validation and CIO review must satisfy the publication gates before a scenario appears.</p>
      {state.loading && <div className="notice objective-loading" role="status">Checking qualified publications…</div>}
      {state.error && <div className="notice error" role="alert"><strong>Objective research unavailable</strong><span>{state.error}</span><button onClick={retry}>Retry objective research</button><p>No scenarios are shown while current qualification cannot be checked.</p></div>}
      {!state.loading && !state.error && report && <ObjectiveResults report={report} now={now} />}
      <nav className="objective-pagination" aria-label="Objective publication pages"><button className="button secondary" disabled={offset === 0 || state.loading} onClick={() => setOffset(value => Math.max(0, value - OBJECTIVE_PAGE_SIZE))}>Previous publications</button><span>Publication page {Math.floor(offset / OBJECTIVE_PAGE_SIZE) + 1}</span><button className="button secondary" disabled={!report?.has_more || state.loading || !!state.error || offset >= 10000} onClick={() => setOffset(value => value + OBJECTIVE_PAGE_SIZE)}>Next publications</button></nav>
    </section>
    <section className="panel"><SectionHeading title="How this comparison is ordered" /><ol className="objective-order">{OBJECTIVE_ORDER.map(rule => <li key={rule}>{rule}</li>)}</ol><p className="small-print">The objective does not affect ordering. This is a comparison of this page’s qualified workspace publications, not a whole-market ranking. Risk/reward is a scenario ratio, not a probability or expected payoff.</p></section>
  </>;
}

export function ObjectiveResults({ report, now }: { report: ObjectiveRanking; now: number }) {
  const current = currentOpportunities(report, now), expired = report.opportunities.length - current.length;
  const reaches = current.some(row => row.gap_to_stretch_profit_gbp === 0);
  return <>
    <p className="objective-updated">Evaluated <time dateTime={report.evaluated_at}>{date(report.evaluated_at)}</time> · {report.examined} publications examined on this page · refreshes every 30 seconds</p>
    <div className="notice" role="status"><strong>{reaches ? "A modelled scenario reaches the aspiration—not a forecast" : "No qualified opportunity on this page currently supports the stretch objective"}</strong><span>{reaches ? "There is no calibrated probability that this scenario or a +500% return will occur." : "An empty comparison is a valid outcome. Money will not relax its gates to fill this page."}</span></div>
    {!!expired && <p className="notice" role="status">{expired} expired {expired === 1 ? "scenario is" : "scenarios are"} hidden. Refresh to re-evaluate the publication page.</p>}
    {current.length ? <div className="objective-scenarios">{current.map((item, index) => <Scenario key={item.research_id} item={item} rank={index + 1} />)}</div> : <EmptyState title="No currently qualified scenarios" detail="Only unexpired, evidence-backed live publications can enter this comparison. No demonstration or personal R&D record is substituted."><Link className="button" href="/jobs">Review research activity</Link></EmptyState>}
    <details className="objective-provenance"><summary>Comparison limitations and provenance</summary><ul>{report.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul><p className="small-print">Comparison hash <code>{report.hash}</code></p></details>
  </>;
}

function Scenario({ item, rank }: { item: Opportunity; rank: number }) {
  return <article className="objective-scenario" aria-labelledby={`objective-${item.research_id}`}><header><div><p className="eyebrow">SCENARIO {rank} ON THIS PAGE · {item.ticker}</p><h3 id={`objective-${item.research_id}`}>{item.company}</h3></div><span className="type-label validation">UNCALIBRATED</span></header>
    <p className="small-print">Hypothetical scenario · not expected returns or personalised investment advice · {item.horizon_days}-day horizon</p>
    <dl className="objective-metrics"><div><dt>Illustrative allocation</dt><dd>{pounds(item.illustrative_allocation_gbp)}</dd><small>{item.percentage_of_assumed_capital.toFixed(1)}% of {pounds(item.assumed_capital_gbp)} assumed capital</small></div><div><dt>Potential net upside</dt><dd>{pounds(item.potential_upside_gbp)}</dd><small>First validated target, after modelled costs</small></div><div><dt>Modelled downside</dt><dd>{pounds(item.modelled_downside_gbp)}</dd><small>Not a maximum possible loss</small></div><div><dt>Gap to £1,000 profit</dt><dd>{pounds(item.gap_to_stretch_profit_gbp)}</dd><small>No extrapolation or extra capital assumed</small></div></dl>
    <dl className="objective-facts"><div><dt>Cost-adjusted risk/reward</dt><dd>{item.risk_reward.toFixed(2)} : 1</dd></div><div><dt>Modelled round-trip costs</dt><dd>{pounds(item.round_trip_cost_gbp)}</dd></div><div><dt>Evidence independence</dt><dd>{item.evidence_independence}</dd></div><div><dt>Snapshot price</dt><dd>{item.raw_price.toLocaleString("en-GB", { maximumFractionDigits: 6 })} {item.raw_currency} · {pounds(item.normalized_price_gbp)}</dd></div><div><dt>Spread assumption</dt><dd>{item.spread_bps.toFixed(2)} bps</dd></div>{item.qlib_rank !== null && <div><dt>Qlib rank</dt><dd>{item.qlib_rank} of {item.qlib_universe_size}</dd></div>}<div><dt>Issued</dt><dd><time dateTime={item.issued_at}>{date(item.issued_at)}</time></dd></div><div><dt>Valid until</dt><dd><time dateTime={item.valid_until}>{date(item.valid_until)}</time></dd></div></dl>
    <div className="objective-actions"><Link className="text-button" href={`/research/${item.research_id}/decision`}>Inspect immutable decision packet <Icon name="arrow" size={14} /></Link><Link className="text-button" href={`/research/${item.research_id}/evidence`}>Inspect {item.evidence_ids.length} evidence references <Icon name="document" size={14} /></Link></div>
    <details className="objective-provenance"><summary>Scenario provenance</summary><p>Normalization: {item.conversion_method}</p><dl><dt>Packet hash</dt><dd><code>{item.packet_hash}</code></dd><dt>Snapshot hash</dt><dd><code>{item.snapshot_hash}</code></dd><dt>Scenario design hash</dt><dd><code>{item.signal_design_hash}</code></dd></dl></details>
  </article>;
}
