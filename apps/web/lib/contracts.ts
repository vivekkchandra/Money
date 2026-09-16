export const JOB_STAGES = ["QUEUED", "ELIGIBILITY_CHECK", "DISCOVERY", "SNAPSHOT_BUILD", "FIRST_PASS_RESEARCH", "FIRST_PASS_LOCKED", "LEAN_VALIDATION", "CREWAI_AUDIT", "CROSS_EXAMINATION", "CONSENSUS", "COMPLETE"] as const;
export type JobStatus = typeof JOB_STAGES[number] | "REJECTED" | "FAILED" | "RUNNING";
export type ResearchState = "STRONG_RESEARCH_CANDIDATE" | "RESEARCH_CANDIDATE" | "WATCH" | "REJECT" | "INSUFFICIENT_EVIDENCE" | "EXPIRED";
export type RecordData = Record<string, unknown>;
export type Job = {
  id: string; ticker: string; status: JobStatus; current_stage: string;
  created_at: string; updated_at: string; snapshot_id?: string | null;
  error_code?: string | null; error_message?: string | null;
  final_state?: ResearchState | null; packet?: RecordData | null;
};
export type Reports = { locked: boolean; reports: Record<string, RecordData>; artifacts: Record<string, unknown>; packet?: RecordData | null };
export type Evidence = { snapshot: RecordData | null; evidence: RecordData[] };
export type Health = { status: string; database: string | RecordData; worker: string | RecordData; mode: string };

export const DEFAULT_MANDATE = {
  broker: "Trading212", account_type: "StocksAndSharesISA", maximum_capital_gbp: "200",
  instrument_types: ["STOCK"], quote_currencies: ["GBP", "GBX"],
  excluded_activities: ["defence", "weapons", "firearms", "material_military_contracting", "oil_exploration", "oil_production", "integrated_oil", "oil_refining", "oil_services"],
  minimum_horizon_days: 1, maximum_horizon_days: 30,
  stretch_profit_gbp: "1000", stretch_may_override_risk: false, version: 1,
};
export type Mandate = typeof DEFAULT_MANDATE;

export function isTerminal(status: string): boolean {
  return ["COMPLETE", "REJECTED", "FAILED"].includes(status);
}

export function humanize(value: string): string {
  return value.toLowerCase().replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

export function signalState(signal: RecordData, now = Date.now()): string {
  const expires = typeof signal.valid_until === "string" ? Date.parse(signal.valid_until) : NaN;
  const issued = typeof signal.issued_at === "string" ? Date.parse(signal.issued_at) : NaN;
  const invalidated = typeof signal.invalidated_at === "string" ? Date.parse(signal.invalidated_at) : NaN;
  if (!Number.isFinite(issued) || issued > now || (Number.isFinite(invalidated) && invalidated <= now)) return "EXPIRED";
  if (!Number.isFinite(expires) || expires <= now) return "EXPIRED";
  return String(signal.state ?? signal.final_state ?? "INSUFFICIENT_EVIDENCE");
}

export function unwrapSignal(row: RecordData): RecordData {
  return row.payload && typeof row.payload === "object" && !Array.isArray(row.payload)
    ? { ...(row.payload as RecordData), research_id: (row.payload as RecordData).research_id ?? row.job_id }
    : row;
}

export function validateJobInput(input: unknown): { ticker: string; mandate?: Mandate } | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const data = input as RecordData;
  if (Object.keys(data).some((key) => !["ticker", "mandate"].includes(key))) return null;
  if (typeof data.ticker !== "string" || !/^[A-Z0-9][A-Z0-9._-]{0,23}$/i.test(data.ticker)) return null;
  if (data.mandate === undefined) return { ticker: data.ticker.toUpperCase() };
  if (!data.mandate || typeof data.mandate !== "object" || Array.isArray(data.mandate)) return null;
  const mandate = data.mandate as Mandate;
  if (Object.keys(mandate).length !== Object.keys(DEFAULT_MANDATE).length) return null;
  const mutable = ["maximum_capital_gbp", "minimum_horizon_days", "maximum_horizon_days"];
  for (const key of Object.keys(DEFAULT_MANDATE) as (keyof Mandate)[]) {
    if (!mutable.includes(key) && JSON.stringify(mandate[key]) !== JSON.stringify(DEFAULT_MANDATE[key])) return null;
  }
  const capital = Number(mandate.maximum_capital_gbp);
  if (!Number.isFinite(capital) || capital <= 0 || capital > 200) return null;
  if (!Number.isInteger(mandate.minimum_horizon_days) || !Number.isInteger(mandate.maximum_horizon_days)) return null;
  if (mandate.minimum_horizon_days < 1 || mandate.maximum_horizon_days > 30 || mandate.minimum_horizon_days > mandate.maximum_horizon_days) return null;
  return { ticker: data.ticker.toUpperCase(), mandate };
}
