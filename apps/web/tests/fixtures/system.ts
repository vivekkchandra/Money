import type { SystemMetrics } from "@/lib/system";

export function systemFixture(): SystemMetrics {
  return {
    scope: "workspace", observed_at: "2026-09-16T12:00:00+00:00",
    jobs: { total: 6, failed: 1, terminal: 4, failure_rate: 0.25, retry_attempts: 2, duration: { samples: 4, mean_seconds: 12.5, max_seconds: 30 } },
    tokens: { source: "budget_reservations", reservations: 3, known_tokens: 1234, unknown_usage_count: 1, reserved_tokens: 3000, unsettled_reserved_tokens: 1000, budget_charged_tokens: 2234 },
    costs: { currency: "GBP", known_total: "0.01234", known_count: 1, unknown_count: 2 },
    signals: { produced: 0, rejected: 1, insufficient_evidence: 3 },
    providers: { scope: "shared_compute_plane", truncated: false, records: [{ provider: "eodhd:ohlcv", circuit: "CLOSED", calls: 4, failures: 1, consecutive_failures: 0, last_latency_seconds: 0.215, last_success: true, updated_at: "2026-09-16T11:59:59+00:00", qualification: "UNKNOWN" }] },
  };
}
