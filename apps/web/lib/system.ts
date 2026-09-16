import { z } from "zod";

const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const duration = z.number().nonnegative().finite().nullable();
const timestamp = z.iso.datetime({ offset: true });

// Whitelist telemetry fields. Never pass arbitrary provider payloads through to the browser.
const schema = z.object({
  scope: z.literal("workspace"), observed_at: timestamp,
  jobs: z.object({
    total: count, failed: count, terminal: count, failure_rate: z.number().min(0).max(1).nullable(),
    retry_attempts: count, duration: z.object({ samples: count, mean_seconds: duration, max_seconds: duration }),
  }),
  tokens: z.object({
    source: z.literal("budget_reservations"), reservations: count, known_tokens: count,
    unknown_usage_count: count, reserved_tokens: count, unsettled_reserved_tokens: count,
    budget_charged_tokens: count,
  }),
  costs: z.object({
    currency: z.literal("GBP"),
    known_total: z.string().max(64).regex(/^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/).refine((value) => Number.isFinite(Number(value))),
    known_count: count, unknown_count: count,
  }),
  signals: z.object({ produced: count, rejected: count, insufficient_evidence: count }),
  providers: z.object({
    scope: z.literal("shared_compute_plane"), truncated: z.boolean(),
    records: z.array(z.object({
      provider: z.string().min(1).max(100).regex(/^[a-zA-Z0-9:._-]+$/),
      circuit: z.enum(["OPEN", "PROBE_DUE", "CLOSED"]),
      calls: count, failures: count, consecutive_failures: count,
      last_latency_seconds: duration, last_success: z.boolean().nullable(),
      updated_at: timestamp, qualification: z.literal("UNKNOWN"),
    })).max(100),
  }),
});

export type SystemMetrics = z.infer<typeof schema>;
export function parseSystemMetrics(value: unknown): SystemMetrics { return schema.parse(value); }

export function measuredDuration(value: number | null): string {
  return value === null ? "Unknown" : `${new Intl.NumberFormat("en-GB", { maximumFractionDigits: 3 }).format(value)} s`;
}

export function knownCost(value: string): string {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", maximumFractionDigits: 8 }).format(Number(value));
}
