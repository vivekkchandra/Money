import { parseInstrumentSearch, type Instrument } from "./instruments";
import type { RecordData } from "./contracts";

export type ReviewedUniverseInstrument = Instrument & { source: string; provider: string; source_id: string; metadata_hash: string; eligibility_proof_hash: string; ethical_proof_hash: string; verified_until: string; instrument_type: "STOCK" };
export type UniversePage = { coverage: "reviewed_manifest"; mode: "live"; complete_broker_universe: false; total: number; offset: number; limit: number; evaluated_at: string; catalogue_hash: string; instruments: ReviewedUniverseInstrument[] } | { coverage: "previously_researched_only"; instruments: RecordData[] };

export function parseUniverse(value: unknown): UniversePage {
  const fail = (): never => { throw new Error("Verified ISA coverage is unavailable. Please retry."); };
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  const data = value as Record<string, unknown>;
  if (data.coverage === "previously_researched_only") {
    if (!Array.isArray(data.instruments) || data.instruments.length > 100 || data.instruments.some(item => !item || typeof item !== "object" || Array.isArray(item))) return fail();
    return { coverage: "previously_researched_only", instruments: data.instruments as RecordData[] };
  }
  if (data.coverage !== "reviewed_manifest" || data.mode !== "live" || data.complete_broker_universe !== false || !Number.isSafeInteger(data.offset) || Number(data.offset) < 0 || Number(data.offset) > 10000) return fail();
  const hash = (item: unknown): string => typeof item === "string" && /^[a-f0-9]{64}$/.test(item) ? item : fail();
  const timestamp = (item: unknown): string => typeof item === "string" && item.length <= 40 && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(item) && Number.isFinite(Date.parse(item)) ? item : fail();
  const text = (item: unknown): string => typeof item === "string" && item.length > 0 && item.length <= 500 && !/[\u0000-\u001f\u007f]/u.test(item) ? item : fail();
  // Reuse canonical identity validation; this directory supports a larger offset.
  const identities = parseInstrumentSearch({ ...data, coverage: "reviewed_catalogue", offset: 0 });
  const rows = data.instruments as Record<string, unknown>[];
  const evaluated = timestamp(data.evaluated_at);
  const instruments = identities.instruments.map((identity, index): ReviewedUniverseInstrument => {
    const row = rows[index], expiry = timestamp(row.verified_until);
    if (!identity.research_allowed || identity.eligibility !== "VERIFIED_ELIGIBLE" || row.instrument_type !== "STOCK" || Date.parse(expiry) <= Date.parse(identity.verified_at) || Date.parse(identity.verified_at) > Date.parse(evaluated)) return fail();
    return { ...identity, source: text(row.source), provider: text(row.provider), source_id: text(row.source_id), metadata_hash: hash(row.metadata_hash), eligibility_proof_hash: hash(row.eligibility_proof_hash), ethical_proof_hash: hash(row.ethical_proof_hash), verified_until: expiry, instrument_type: "STOCK" };
  });
  return { coverage: "reviewed_manifest", mode: "live", complete_broker_universe: false, instruments, total: identities.total, offset: Number(data.offset), limit: identities.limit, evaluated_at: evaluated, catalogue_hash: hash(data.catalogue_hash) };
}

export function currentUniverse(page: Extract<UniversePage, { mode: "live" }>, now: number): ReviewedUniverseInstrument[] {
  return page.instruments.filter(item => Date.parse(item.verified_at) <= now && Date.parse(item.verified_until) > now);
}
