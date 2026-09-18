/** The reviewed catalogue is discovery, not permission to bypass research gates. */
export type Instrument = {
  instrument_id: string;
  ticker: string;
  company: string;
  exchange: string | null;
  currency: string;
  eligibility: "VERIFIED_ELIGIBLE" | "VERIFIED_INELIGIBLE" | "UNKNOWN";
  research_allowed: boolean;
  verified_at: string;
  synthetic: boolean;
  research_mode?: "live_rnd";
  provider_symbol?: string;
  canonical_symbol?: string;
  country?: string | null;
  instrument_type?: string;
};

export type InstrumentSearch = {
  instruments: Instrument[];
  total: number;
  limit: number;
  offset: number;
  coverage: "reviewed_catalogue" | "public_provider";
  mode: "live" | "demo" | "live_rnd";
};

const TICKER = /^[A-Z0-9][A-Z0-9._-]{0,31}$/;
const hasControl = (value: string) => [...value].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);

export function instrumentQuery(input: URLSearchParams): URLSearchParams | null {
  if ([...input.keys()].some(key => !["query", "limit", "offset"].includes(key))) return null;
  if (["query", "limit", "offset"].some(key => input.getAll(key).length > 1)) return null;
  const query = input.get("query")?.trim() ?? "";
  if (!query || query.length > 80 || hasControl(query)) return null;
  const limit = input.get("limit") ?? "10";
  const offset = input.get("offset") ?? "0";
  if (!/^[1-9]\d?$/.test(limit) || Number(limit) > 20) return null;
  if (!/^(?:0|[1-9]\d{0,3})$/.test(offset) || Number(offset) > 1000) return null;
  return new URLSearchParams({ query, limit, offset });
}

function text(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= maximum && !hasControl(value);
}

/** Strict bounds and field projection avoid leaking unexpected provider payloads. */
export function parseInstrumentSearch(value: unknown): InstrumentSearch {
  const invalid = () => { throw new Error("Instrument search is temporarily unavailable. Please retry."); };
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const data = value as Record<string, unknown>;
  if (!(["live", "demo"].includes(String(data.mode)) && data.coverage === "reviewed_catalogue") && !(data.mode === "live_rnd" && data.coverage === "public_provider")) return invalid();
  if (
      !Number.isSafeInteger(data.total) || Number(data.total) < 0 ||
      !Number.isSafeInteger(data.limit) || Number(data.limit) < 1 || Number(data.limit) > 20 ||
      !Number.isSafeInteger(data.offset) || Number(data.offset) < 0 || Number(data.offset) > 1000 ||
      !Array.isArray(data.instruments) || data.instruments.length > Number(data.limit) ||
      data.instruments.length > Number(data.total)) return invalid();
  const seen = new Set<string>();
  const instruments = data.instruments.map((item): Instrument => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return invalid();
    const row = item as Record<string, unknown>;
    if (!text(row.instrument_id, 32) || !TICKER.test(row.instrument_id) ||
        !text(row.ticker, 32) || !TICKER.test(row.ticker) || row.instrument_id !== row.ticker ||
        seen.has(row.instrument_id) || !text(row.company, 256) ||
        !(row.exchange === null || text(row.exchange, 64)) ||
        !text(row.currency, 8) || !/^[A-Z]{3,8}$/.test(row.currency) ||
        !["VERIFIED_ELIGIBLE", "VERIFIED_INELIGIBLE", "UNKNOWN"].includes(String(row.eligibility)) ||
        typeof row.research_allowed !== "boolean" || typeof row.synthetic !== "boolean" ||
        !text(row.verified_at, 40) || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(row.verified_at) ||
        !Number.isFinite(Date.parse(row.verified_at))) return invalid();
    if (data.mode !== "live_rnd" && row.research_allowed && (row.eligibility !== "VERIFIED_ELIGIBLE" || !["GBP", "GBX"].includes(row.currency))) return invalid();
    if (data.mode !== "demo" ? (row.synthetic || row.ticker === "DEMO.L") : (!row.synthetic || row.ticker !== "DEMO.L")) return invalid();
    const metadata: Pick<Instrument, "provider_symbol" | "canonical_symbol" | "country" | "instrument_type"> = {};
    if (data.mode === "live_rnd") {
      for (const key of ["provider_symbol", "canonical_symbol", "country", "instrument_type"] as const) {
        if (row[key] !== undefined) {
          if (key === "country" && row[key] === null) metadata.country = null;
          else if (text(row[key], 100)) metadata[key] = row[key];
          else return invalid();
        }
      }
    }
    seen.add(row.instrument_id);
    return {
      instrument_id: row.instrument_id, ticker: row.ticker, company: row.company,
      exchange: row.exchange, currency: row.currency,
      eligibility: row.eligibility as Instrument["eligibility"], research_allowed: row.research_allowed,
      verified_at: row.verified_at, synthetic: row.synthetic,
      ...(data.mode === "live_rnd" ? { research_mode: "live_rnd" as const, ...metadata } : {}),
    };
  });
  return { instruments, total: Number(data.total), limit: Number(data.limit), offset: Number(data.offset), coverage: data.coverage as InstrumentSearch["coverage"], mode: data.mode as InstrumentSearch["mode"] };
}

export function instrumentLabel(instrument: Instrument): string {
  return `${instrument.company} (${instrument.ticker})`;
}

export function instrumentStatus(instrument: Instrument): string {
  if (instrument.synthetic) return "Synthetic demonstration · not live research";
  if (instrument.research_mode === "live_rnd") return `R&D / PERSONAL USE · ${instrument.eligibility === "UNKNOWN" ? "Research eligibility unverified" : instrument.eligibility === "VERIFIED_INELIGIBLE" ? "Outside the research universe" : "Research eligibility verified"} · ${instrument.research_allowed ? "public-data collection only" : "collection unavailable"}`;
  if (instrument.eligibility === "UNKNOWN") return "Research eligibility not verified · research unavailable";
  if (instrument.eligibility === "VERIFIED_INELIGIBLE") return "Outside the eligible research universe";
  return instrument.research_allowed ? "Research eligibility verified · screening still applies" : "Broker membership verified · research qualification incomplete";
}

export type SearchState = {
  query: string; revision: number; phase: "idle" | "loading" | "ready" | "error" | "selected";
  results: Instrument[]; total: number; active: number; open: boolean; error: string | null;
  mode: InstrumentSearch["mode"] | null;
};
export const INITIAL_SEARCH: SearchState = { query: "", revision: 0, phase: "idle", results: [], total: 0, active: -1, open: false, error: null, mode: null };
export type SearchAction =
  | { type: "query"; query: string }
  | { type: "retry" }
  | { type: "loaded"; revision: number; result: InstrumentSearch }
  | { type: "failed"; revision: number; error: string }
  | { type: "select"; instrument: Instrument; allowUnavailable?: boolean }
  | { type: "move"; direction: 1 | -1; allowUnavailable?: boolean }
  | { type: "close" }
  | { type: "open" };

export function searchReducer(state: SearchState, action: SearchAction): SearchState {
  switch (action.type) {
    case "query": return { ...INITIAL_SEARCH, query: action.query.slice(0, 80), revision: state.revision + 1, phase: action.query.trim() ? "loading" : "idle", open: !!action.query.trim() };
    case "retry": return { ...state, revision: state.revision + 1, phase: state.query.trim() ? "loading" : "idle", error: null, open: !!state.query.trim() };
    case "loaded": return action.revision !== state.revision ? state : { ...state, phase: "ready", results: action.result.instruments, total: action.result.total, active: -1, error: null, mode: action.result.mode };
    case "failed": return action.revision !== state.revision ? state : { ...state, phase: "error", results: [], active: -1, error: action.error };
    case "select": return (!action.allowUnavailable && !action.instrument.research_allowed) || !state.results.some(item => item === action.instrument) ? state : { ...state, query: instrumentLabel(action.instrument), phase: "selected", active: -1, open: false, revision: state.revision + 1 };
    case "close": return { ...state, open: false, active: -1 };
    case "open": return { ...state, open: state.phase !== "selected" && !!state.query.trim() };
    case "move": {
      const eligible = state.results.map((item, index) => action.allowUnavailable || item.research_allowed ? index : -1).filter(index => index >= 0);
      if (!eligible.length) return { ...state, open: true, active: -1 };
      const position = eligible.indexOf(state.active);
      const next = position < 0 ? (action.direction === 1 ? 0 : eligible.length - 1) : (position + action.direction + eligible.length) % eligible.length;
      return { ...state, active: eligible[next], open: true };
    }
  }
}
