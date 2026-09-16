/** Display-only projection of gated live publications. Never a research input. */
export const OBJECTIVE_PAGE_SIZE = 20;
export const OBJECTIVE_POLL_MS = 30_000;
export const OBJECTIVE_ORDER = [
  "Cost-adjusted risk/reward descending", "Modelled downside GBP ascending",
  "Cost-adjusted hypothetical upside GBP descending", "Ticker and research ID ascending for reproducible ties",
] as const;

export type Opportunity = {
  research_id: string; ticker: string; company: string; packet_hash: string; snapshot_hash: string; signal_design_hash: string;
  state: "RESEARCH_CANDIDATE"; raw_price: number; raw_currency: "GBP" | "GBX"; normalized_price_gbp: number; conversion_method: string;
  assumed_capital_gbp: number; illustrative_allocation_gbp: number; percentage_of_assumed_capital: number;
  modelled_downside_gbp: number; potential_upside_gbp: number; scenario_return_fraction: number; risk_reward: number;
  round_trip_cost_gbp: number; horizon_days: number; issued_at: string; valid_until: string;
  evidence_independence: "MEDIUM" | "HIGH"; qlib_rank: number | null; qlib_universe_size: number | null; spread_bps: number;
  gap_to_stretch_profit_gbp: number; calibration: "UNCALIBRATED"; probability: null; expected_payoff_gbp: null; evidence_ids: string[];
};
export type ObjectiveRanking = {
  objective: { starting_capital: 200; target_profit: 1000; target_end_value: 1200; target_return: 5; horizon_days: 30; can_override_risk: false };
  basis: "RESEARCH_SIMULATION_NOT_ACTUAL_PORTFOLIO"; coverage: "PAGINATED_WORKSPACE_PUBLICATIONS"; order: readonly string[];
  version: "qualified-scenarios-v1"; evaluated_at: string; examined: number; offset: number; has_more: boolean;
  opportunities: Opportunity[]; limitations: string[]; hash: string;
};

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid objective record");
  return value as Record<string, unknown>;
}
function text(value: unknown, maximum = 300): string {
  if (typeof value !== "string" || !value.length || value.length > maximum || /[\u0000-\u001f\u007f]/u.test(value)) throw new Error("Invalid objective text");
  return value;
}
function number(value: unknown, minimum = 0, maximum = 1e12): number {
  if (typeof value !== "number" && (typeof value !== "string" || !/^-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?$/.test(value) || value.length > 80)) throw new Error("Invalid objective number");
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < minimum || parsed > maximum) throw new Error("Objective number outside bounds");
  return parsed;
}
function integer(value: unknown, maximum = 10000): number {
  const parsed = number(value, 0, maximum);
  if (!Number.isInteger(parsed)) throw new Error("Invalid objective integer");
  return parsed;
}
function timestamp(value: unknown): string {
  const parsed = text(value, 40);
  if (!/(?:Z|[+-]\d{2}:\d{2})$/.test(parsed) || !Number.isFinite(Date.parse(parsed))) throw new Error("Invalid objective timestamp");
  return parsed;
}
function hash(value: unknown): string {
  const parsed = text(value, 64);
  if (!/^[a-f0-9]{64}$/.test(parsed)) throw new Error("Invalid objective hash");
  return parsed;
}
function strings(value: unknown, maximum: number, width: number): string[] {
  if (!Array.isArray(value) || value.length > maximum) throw new Error("Invalid objective list");
  return value.map(item => text(item, width));
}

function opportunity(value: unknown): Opportunity {
  const row = record(value);
  const id = text(row.research_id, 36), ticker = text(row.ticker, 32);
  if (!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(id) || !/^[A-Z0-9][A-Z0-9._-]{0,31}$/.test(ticker) || ticker === "DEMO.L") throw new Error("Invalid objective identity");
  if (row.state !== "RESEARCH_CANDIDATE" || !["GBP", "GBX"].includes(String(row.raw_currency)) || row.calibration !== "UNCALIBRATED" || row.probability !== null || row.expected_payoff_gbp !== null || !["MEDIUM", "HIGH"].includes(String(row.evidence_independence))) throw new Error("Unqualified objective scenario");
  const capital = number(row.assumed_capital_gbp, Number.MIN_VALUE, 200), allocation = number(row.illustrative_allocation_gbp, Number.MIN_VALUE, capital);
  const raw = number(row.raw_price, Number.MIN_VALUE), normalized = number(row.normalized_price_gbp, Number.MIN_VALUE);
  if (Math.abs(normalized - raw / (row.raw_currency === "GBX" ? 100 : 1)) > Math.max(1e-9, normalized * 1e-10)) throw new Error("Objective currency mismatch");
  const issued = timestamp(row.issued_at), expiry = timestamp(row.valid_until);
  if (Date.parse(expiry) <= Date.parse(issued)) throw new Error("Invalid objective expiry");
  const horizon = integer(row.horizon_days, 30);
  if (horizon < 1) throw new Error("Invalid objective horizon");
  const rank = row.qlib_rank === null ? null : integer(row.qlib_rank, 1e7), universe = row.qlib_universe_size === null ? null : integer(row.qlib_universe_size, 1e7);
  if ((rank === null) !== (universe === null) || (rank !== null && (rank < 1 || universe === null || rank > universe))) throw new Error("Invalid quant rank");
  return {
    research_id: id, ticker, company: text(row.company), packet_hash: hash(row.packet_hash), snapshot_hash: hash(row.snapshot_hash), signal_design_hash: hash(row.signal_design_hash),
    state: "RESEARCH_CANDIDATE", raw_price: raw, raw_currency: row.raw_currency as "GBP" | "GBX", normalized_price_gbp: normalized, conversion_method: text(row.conversion_method),
    assumed_capital_gbp: capital, illustrative_allocation_gbp: allocation, percentage_of_assumed_capital: number(row.percentage_of_assumed_capital, Number.MIN_VALUE, 100),
    modelled_downside_gbp: number(row.modelled_downside_gbp, 0, 200), potential_upside_gbp: number(row.potential_upside_gbp, Number.MIN_VALUE), scenario_return_fraction: number(row.scenario_return_fraction), risk_reward: number(row.risk_reward, Number.MIN_VALUE),
    round_trip_cost_gbp: number(row.round_trip_cost_gbp), horizon_days: horizon, issued_at: issued, valid_until: expiry,
    evidence_independence: row.evidence_independence as "MEDIUM" | "HIGH", qlib_rank: rank, qlib_universe_size: universe, spread_bps: number(row.spread_bps, 0, 10000),
    gap_to_stretch_profit_gbp: number(row.gap_to_stretch_profit_gbp, 0, 1000), calibration: "UNCALIBRATED", probability: null, expected_payoff_gbp: null, evidence_ids: strings(row.evidence_ids, 4000, 256),
  };
}

export function parseObjectiveRanking(value: unknown): ObjectiveRanking {
  const report = record(value), objective = record(report.objective);
  if (report.version !== "qualified-scenarios-v1" || report.basis !== "RESEARCH_SIMULATION_NOT_ACTUAL_PORTFOLIO" || report.coverage !== "PAGINATED_WORKSPACE_PUBLICATIONS" || typeof report.has_more !== "boolean") throw new Error("Invalid objective basis");
  if (number(objective.starting_capital) !== 200 || number(objective.target_profit) !== 1000 || number(objective.target_end_value) !== 1200 || number(objective.target_return) !== 5 || objective.horizon_days !== 30 || objective.can_override_risk !== false) throw new Error("Objective cannot override research boundaries");
  if (!Array.isArray(report.opportunities) || report.opportunities.length > OBJECTIVE_PAGE_SIZE || JSON.stringify(report.order) !== JSON.stringify(OBJECTIVE_ORDER)) throw new Error("Invalid objective ordering");
  const rows = report.opportunities.map(opportunity), examined = integer(report.examined, OBJECTIVE_PAGE_SIZE), evaluated = timestamp(report.evaluated_at);
  if (rows.length > examined || new Set(rows.map(row => row.research_id)).size !== rows.length || rows.some(row => Date.parse(row.issued_at) > Date.parse(evaluated))) throw new Error("Invalid objective coverage");
  return { objective: { starting_capital: 200, target_profit: 1000, target_end_value: 1200, target_return: 5, horizon_days: 30, can_override_risk: false }, basis: "RESEARCH_SIMULATION_NOT_ACTUAL_PORTFOLIO", coverage: "PAGINATED_WORKSPACE_PUBLICATIONS", order: OBJECTIVE_ORDER, version: "qualified-scenarios-v1", evaluated_at: evaluated, examined, offset: integer(report.offset), has_more: report.has_more, opportunities: rows, limitations: strings(report.limitations, 20, 1000), hash: hash(report.hash) };
}

export function objectiveQuery(parameters: URLSearchParams): URLSearchParams | null {
  if ([...parameters.keys()].some(key => !["limit", "offset"].includes(key)) || parameters.getAll("limit").length > 1 || parameters.getAll("offset").length > 1) return null;
  const limit = parameters.get("limit") ?? "20", offset = parameters.get("offset") ?? "0";
  if (!/^[1-9]\d?$/.test(limit) || Number(limit) > 20 || !/^(0|[1-9]\d{0,4})$/.test(offset) || Number(offset) > 10000) return null;
  return new URLSearchParams({ limit, offset });
}

export function currentOpportunities(report: ObjectiveRanking, now: number): Opportunity[] {
  return report.opportunities.filter(row => Date.parse(row.issued_at) <= now && Date.parse(row.valid_until) > now);
}

export type ObjectiveLoad = { report: ObjectiveRanking | null; error: string | null; loading: boolean };
/** Non-overlapping polling; abort and ignore responses from a previous page/session. */
export function observeObjective(offset: number, load: (path: string, signal: AbortSignal) => Promise<unknown>, receive: (state: ObjectiveLoad) => void): () => void {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  receive({ report: null, error: null, loading: true });
  async function poll() {
    try {
      const report = parseObjectiveRanking(await load(`/api/research/objective?limit=20&offset=${offset}`, controller.signal));
      if (report.offset !== offset) throw new Error("The research service returned a different page. Please retry.");
      if (!controller.signal.aborted) receive({ report, error: null, loading: false });
    } catch (error) {
      if (!controller.signal.aborted) receive({ report: null, error: error instanceof Error ? error.message : "Objective research is unavailable. Please retry.", loading: false });
    } finally {
      if (!controller.signal.aborted) timer = setTimeout(() => void poll(), OBJECTIVE_POLL_MS);
    }
  }
  void poll();
  return () => { controller.abort(); if (timer) clearTimeout(timer); };
}
