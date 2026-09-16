import { knownCost, measuredDuration, type SystemMetrics } from "@/lib/system";
import { Badge, DataRecord, EmptyState, SectionHeading } from "./primitives";

export function OperationalMetrics({ metrics, error, loading, onRefresh }: {
  metrics: SystemMetrics | null; error: string | null; loading: boolean; onRefresh: () => void;
}) {
  return <section className="panel">
    <SectionHeading title="Measured operations" action={<button className="text-button" onClick={onRefresh}>Refresh measurements</button>} />
    {error && <div className="notice error" role="alert">{error}{metrics && " Values below are the last successful observation, not current state."}</div>}
    {loading && <p className="notice" role="status">Loading durable operational measurements…</p>}
    {metrics ? <>
      <p className="small-print">Workspace totals · Last observed <time dateTime={metrics.observed_at}>{metrics.observed_at}</time>. Counts include demonstration runs when this workspace uses demo mode.</p>
      <div className="health-grid">
        <section className="health-card"><h3>Job reliability</h3><DataRecord data={{ total_jobs: metrics.jobs.total, terminal_jobs: metrics.jobs.terminal, failed_jobs: metrics.jobs.failed, failure_rate: metrics.jobs.failure_rate === null ? "Unknown — no terminal sample" : `${(metrics.jobs.failure_rate * 100).toFixed(1)}%`, retry_attempts: metrics.jobs.retry_attempts, duration_sample_size: metrics.jobs.duration.samples, mean_duration: measuredDuration(metrics.jobs.duration.mean_seconds), maximum_duration: measuredDuration(metrics.jobs.duration.max_seconds) }} /></section>
        <section className="health-card"><h3>Token accounting</h3><DataRecord data={{ measured_tokens: metrics.tokens.known_tokens, unknown_usage_invocations: metrics.tokens.unknown_usage_count, reservations: metrics.tokens.reservations, budget_charged_tokens: metrics.tokens.budget_charged_tokens, unsettled_reserved_tokens: metrics.tokens.unsettled_reserved_tokens }} /><p className="small-print">Recorded budget reservations only. Unknown usage remains charged at its reserved maximum; it is not zero usage.</p></section>
        <section className="health-card"><h3>Inference cost</h3><DataRecord data={{ known_cost_subtotal: knownCost(metrics.costs.known_total), measured_cost_invocations: metrics.costs.known_count, unknown_cost_invocations: metrics.costs.unknown_count }} /><p className="small-print">This subtotal excludes unknown costs. It is not a provider invoice or complete spend figure.</p></section>
        <section className="health-card"><h3>Research output</h3><DataRecord data={{ signals_produced: metrics.signals.produced, research_rejected: metrics.signals.rejected, insufficient_evidence: metrics.signals.insufficient_evidence }} /><p className="small-print">Research records only. No transactions or user positions are inferred.</p></section>
      </div>
      <h3>Provider observations</h3><p className="small-print">Shared compute-plane measurements, across workspaces. A closed circuit or successful call does not establish production qualification.</p>
      {metrics.providers.records.length ? <div className="health-grid">{metrics.providers.records.map((provider) => <section className="health-card" key={provider.provider}>
        <h4>{provider.provider}</h4><div className="health-summary"><Badge value={provider.circuit} label={`Circuit: ${provider.circuit.toLowerCase().replaceAll("_", " ")}`} /><Badge value="UNKNOWN" label="Qualification unknown" /></div>
        <DataRecord data={{ calls: provider.calls, failures: provider.failures, consecutive_failures: provider.consecutive_failures, last_latency: measuredDuration(provider.last_latency_seconds), last_call: provider.last_success === null ? "Not observed" : provider.last_success ? "Succeeded" : "Failed", last_observed: provider.updated_at }} />
      </section>)}</div> : <EmptyState title="No provider observations yet" detail="No availability or qualification is inferred from an empty observation history." icon="pulse" />}
      {metrics.providers.truncated && <p className="notice" role="status">The provider list is bounded. Additional provider observations are not shown.</p>}
    </> : !loading && <EmptyState title="Operational measurements unavailable" detail="No zero values or healthy status are inferred. Retry when the research service is reachable." icon="pulse" />}
  </section>;
}
