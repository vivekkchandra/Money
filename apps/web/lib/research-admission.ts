/** Read-only projection of an operator-generated local research artifact.
 * This contract never replaces the reviewed production-universe parser.
 */
export const RESEARCH_STATES = ["DISCOVERED", "RESEARCH_ELIGIBLE", "RESEARCHED", "BACKTEST_READY", "LEAN_VALIDATED"] as const;
export type ResearchAdmissionState = typeof RESEARCH_STATES[number];
export const ENRICHMENT_STATES = ["AVAILABLE", "UNAVAILABLE", "NOT_CONFIGURED", "ACCESS_DENIED", "STALE"] as const;
export type OptionalEnrichmentState = typeof ENRICHMENT_STATES[number];
const SOURCES = ["eodhd_market_data", "eodhd_fundamentals", "companies_house", "official_disclosures", "news"] as const;

export type ResearchTestingView = {
  purpose: "RESEARCH_TESTING";
  status: "RESEARCH RUN COMPLETE" | "RESEARCH RUN BLOCKED";
  state: ResearchAdmissionState;
  researchEligible: number;
  rawInstruments: number | null;
  gbpGbxStocks: number | null;
  identityConflicts: number | null;
  enrichment: { source: typeof SOURCES[number]; counts: Partial<Record<OptionalEnrichmentState, number>> }[];
  instruments: { trading212Id: string; state: ResearchAdmissionState; independentReports: number; leanExecuted: boolean; ethicalStatus: "NOT_SCREENED" | "PASS" | "FAIL" | "UNKNOWN" | null }[];
  productionReady: false;
  qlibEnabled: false;
  leanMandatory: true;
};

export const RESEARCH_STATE_LABELS: Record<ResearchAdmissionState, string> = {
  DISCOVERED: "Discovered — research admission not yet established",
  RESEARCH_ELIGIBLE: "Research eligible — not production approved",
  RESEARCHED: "Independent research recorded — not LEAN validated",
  BACKTEST_READY: "Backtest inputs ready — LEAN execution still required",
  LEAN_VALIDATED: "LEAN validated for research — not release or trading approval",
};

export function parseResearchTesting(value: unknown): ResearchTestingView {
  const fail = (): never => { throw new Error("This file is not a valid local research/testing summary. No approval is inferred."); };
  const record = (item: unknown): Record<string, unknown> => item !== null && typeof item === "object" && !Array.isArray(item) ? item as Record<string, unknown> : fail();
  const count = (item: unknown): number => Number.isSafeInteger(item) && Number(item) >= 0 && Number(item) <= 1_000_000 ? Number(item) : fail();
  const optionalCount = (item: unknown): number | null => item === undefined ? null : count(item);
  const state = (item: unknown): ResearchAdmissionState => RESEARCH_STATES.includes(item as ResearchAdmissionState) ? item as ResearchAdmissionState : fail();
  const data = record(value);
  if (data.purpose !== "RESEARCH_TESTING" || data.production_ready !== false || data.qlib_enabled !== false || data.lean_mandatory !== true || data.manifest_sha256 !== null || !["RESEARCH RUN COMPLETE", "RESEARCH RUN BLOCKED"].includes(String(data.status))) return fail();
  const summary = record(data.summary);
  const researchEligible = count(data.research_eligible);
  const rawInstruments = optionalCount(summary.raw_instruments);
  const gbpGbxStocks = optionalCount(summary.gbp_gbx_stocks);
  const identityConflicts = optionalCount(summary.research_identity_conflicts);
  if (gbpGbxStocks !== null && (researchEligible > gbpGbxStocks || (rawInstruments !== null && gbpGbxStocks > rawInstruments) || (identityConflicts !== null && identityConflicts > gbpGbxStocks))) return fail();
  const coverage = summary.enrichment_coverage === undefined ? {} : record(summary.enrichment_coverage);
  const enrichment = SOURCES.filter(source => coverage[source] !== undefined).map(source => {
    const input = record(coverage[source]);
    if (Object.keys(input).some(key => !ENRICHMENT_STATES.includes(key as OptionalEnrichmentState))) return fail();
    const counts: Partial<Record<OptionalEnrichmentState, number>> = {};
    for (const status of ENRICHMENT_STATES) if (input[status] !== undefined) counts[status] = count(input[status]);
    if (gbpGbxStocks !== null && Object.values(counts).reduce((total, item) => total + item, 0) > gbpGbxStocks) return fail();
    return { source, counts };
  });
  if (!Array.isArray(data.results) || data.results.length > 20) return fail();
  const instruments = data.results.map(item => {
    const row = record(item);
    const id = row.trading212_id;
    if (typeof id !== "string" || !/^[A-Za-z0-9_.-]{1,100}$/.test(id)) return fail();
    const firstPass = record(row.first_pass);
    if (typeof firstPass.complete !== "boolean") return fail();
    const lean = record(row.lean);
    if (lean.state !== undefined && !["BLOCKED", "BACKTEST_READY", "LEAN_EXECUTED", "LEAN_VALIDATED"].includes(String(lean.state))) return fail();
    const readiness = lean.readiness === undefined ? null : record(lean.readiness);
    if (readiness && readiness.state !== "BLOCKED" && readiness.state !== "BACKTEST_READY") return fail();
    if (["BACKTEST_READY", "LEAN_EXECUTED", "LEAN_VALIDATED"].includes(String(lean.state)) && readiness?.state === "BLOCKED") return fail();
    const leanExecuted = lean.execution_performed === true || lean.executed === true;
    if (leanExecuted && !firstPass.complete) return fail();
    if (lean.state === "LEAN_VALIDATED" && (!leanExecuted || !firstPass.complete)) return fail();
    const reports = firstPass.reports === undefined ? [] : firstPass.reports;
    if (!Array.isArray(reports) || reports.length > 2) return fail();
    const firms = reports.map(report => record(report).firm);
    if (new Set(firms).size !== firms.length || firms.some(firm => firm !== "tradingagents" && firm !== "ai_hedge_fund") || (firstPass.complete && firms.length !== 2)) return fail();
    const backtestReady = lean.state === "BACKTEST_READY" || readiness?.state === "BACKTEST_READY";
    const inferred = lean.state === "LEAN_VALIDATED" ? "LEAN_VALIDATED" : backtestReady && firstPass.complete ? "BACKTEST_READY" : firstPass.complete ? "RESEARCHED" : "RESEARCH_ELIGIBLE";
    const explicitState = row.state === undefined ? row.research_state : row.state;
    if (row.state !== undefined && row.research_state !== undefined && row.state !== row.research_state) return fail();
    const researchState = explicitState === undefined ? inferred : state(explicitState);
    if (researchState !== inferred) return fail();
    const ethics = row.ethical_status;
    if (ethics !== undefined && !["NOT_SCREENED", "PASS", "FAIL", "UNKNOWN"].includes(String(ethics))) return fail();
    return { trading212Id: id, state: researchState as ResearchAdmissionState, independentReports: reports.length, leanExecuted, ethicalStatus: ethics === undefined ? null : ethics as "NOT_SCREENED" | "PASS" | "FAIL" | "UNKNOWN" };
  });
  if (new Set(instruments.map(item => item.trading212Id)).size !== instruments.length) return fail();
  const overall = state(data.state);
  if (instruments.length > researchEligible || (overall !== "DISCOVERED" && researchEligible === 0)) return fail();
  if (instruments.length && RESEARCH_STATES.indexOf(overall) !== Math.min(...instruments.map(item => RESEARCH_STATES.indexOf(item.state)))) return fail();
  if (RESEARCH_STATES.indexOf(overall) >= 2 && !instruments.length) return fail();
  if (data.status === "RESEARCH RUN COMPLETE" && (overall !== "LEAN_VALIDATED" || !instruments.length || instruments.some(item => item.state !== "LEAN_VALIDATED"))) return fail();
  return { purpose: "RESEARCH_TESTING", status: data.status as ResearchTestingView["status"], state: overall, researchEligible, rawInstruments, gbpGbxStocks, identityConflicts, enrichment, instruments, productionReady: false, qlibEnabled: false, leanMandatory: true };
}
