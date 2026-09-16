import type { RecordData } from "./contracts";

export type PersonalRndPacket = {
  research_id: string; ticker: string; issued_at: string; snapshot_id: string; snapshot_hash: string;
  runtime: "live_rnd"; purpose: "PERSONAL_RND"; final_state: "INSUFFICIENT_EVIDENCE"; signal: null;
  limitations: string[]; reasons: string[];
  components: { component: string; status: string; reason: string }[];
  analysis: Record<string, string | number | boolean>;
};

const boundedString = (value: unknown, maximum = 1000): value is string => typeof value === "string" && value.length > 0 && value.length <= maximum;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.length <= 64 && value.every(item => boundedString(item));

/** The R&D record must never be mistaken for a qualified signal/decision packet. */
export function parsePersonalRnd(value: unknown): PersonalRndPacket | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as RecordData;
  if (row.runtime !== "live_rnd" || row.purpose !== "PERSONAL_RND" || row.final_state !== "INSUFFICIENT_EVIDENCE" || row.signal !== null ||
      !boundedString(row.research_id, 64) || !boundedString(row.ticker, 32) || !boundedString(row.snapshot_id, 128) ||
      !boundedString(row.snapshot_hash, 128) || !/^[a-f0-9]{64}$/i.test(row.snapshot_hash) ||
      !boundedString(row.issued_at, 40) || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(row.issued_at) || !Number.isFinite(Date.parse(row.issued_at)) ||
      !strings(row.limitations) || !strings(row.reasons) || !Array.isArray(row.components) || row.components.length > 32 ||
      !row.analysis || typeof row.analysis !== "object" || Array.isArray(row.analysis)) return null;
  const components: PersonalRndPacket["components"] = [];
  for (const item of row.components) {
    if (!item || typeof item !== "object" || !boundedString(item.component, 80) || !boundedString(item.status, 40) || !/^[A-Z][A-Z_]+$/.test(item.status) || !boundedString(item.reason)) return null;
    components.push({ component: item.component, status: item.status, reason: item.reason });
  }
  const analysis: PersonalRndPacket["analysis"] = {};
  for (const key of ["observations", "first_close", "last_close", "price_change_fraction", "volume_observations", "rsi", "volatility", "normalized_gbp"] as const) {
    const fact = (row.analysis as RecordData)[key];
    if (fact !== undefined && fact !== null) {
      if (typeof fact !== "number" || !Number.isFinite(fact) || (key !== "price_change_fraction" && fact < 0)) return null;
      analysis[key] = fact;
    }
  }
  const currency = (row.analysis as RecordData).raw_currency;
  if (currency !== undefined && currency !== null) {
    if (!boundedString(currency, 8) || !/^[A-Za-z]{3,8}$/.test(currency)) return null;
    analysis.raw_currency = currency;
  }
  const split = (row.analysis as RecordData).split_events_present;
  if (split !== undefined) {
    if (typeof split !== "boolean") return null;
    analysis.split_events_present = split;
  }
  const semantics = (row.analysis as RecordData).return_semantics;
  if (semantics !== undefined) {
    if (!boundedString(semantics, 500)) return null;
    analysis.return_semantics = semantics;
  }
  return { research_id: row.research_id, ticker: row.ticker, issued_at: row.issued_at, snapshot_id: row.snapshot_id, snapshot_hash: row.snapshot_hash, runtime: "live_rnd", purpose: "PERSONAL_RND", final_state: "INSUFFICIENT_EVIDENCE", signal: null, limitations: row.limitations, reasons: row.reasons, components, analysis };
}
