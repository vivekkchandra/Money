import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OperationalMetrics } from "@/components/operational-metrics";
import { knownCost, measuredDuration, parseSystemMetrics } from "@/lib/system";
import { systemFixture } from "./fixtures/system";

describe("measured system operations", () => {
  it("separates observed usage, unknown cost and unverified qualification", () => {
    const html = renderToStaticMarkup(<OperationalMetrics metrics={systemFixture()} error={null} loading={false} onRefresh={() => {}} />);
    expect(html).toContain("£0.01234");
    expect(html).toContain("25.0%");
    expect(html).toContain("0.215 s");
    expect(html).toContain("excludes unknown costs");
    expect(html).toContain("Qualification unknown");
    expect(html).toContain("Shared compute-plane measurements, across workspaces");
    expect(html).toContain("not zero usage");
    expect(html).not.toContain("Production ready");
  });
  it("reports unavailable and loading states without invented zero measurements", () => {
    const html = renderToStaticMarkup(<OperationalMetrics metrics={null} error="Research service is unavailable" loading={false} onRefresh={() => {}} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("Operational measurements unavailable");
    expect(html).not.toContain("£0");
    const loading = renderToStaticMarkup(<OperationalMetrics metrics={null} error={null} loading onRefresh={() => {}} />);
    expect(loading).toContain('role="status"');
    expect(loading).toContain("Loading durable operational measurements");
  });
  it("does not show an all-unknown invoice or estimate as zero cost", () => {
    const metrics = systemFixture();
    metrics.costs = { ...metrics.costs, known_total: "0", known_count: 0, unknown_count: 3 };
    const html = renderToStaticMarkup(<OperationalMetrics metrics={metrics} error={null} loading={false} onRefresh={() => {}} />);
    expect(html).toContain("Cost estimates");
    expect(html).toContain("Actual invoiced costs remain unknown");
    expect(html).not.toContain("£0");
  });
  it("labels a failed refresh as stale, not current healthy state", () => {
    const html = renderToStaticMarkup(<OperationalMetrics metrics={systemFixture()} error="Refresh failed" loading={false} onRefresh={() => {}} />);
    expect(html).toContain("last successful observation, not current state");
  });
  it("does not qualify providers from an empty or truncated observation set", () => {
    const metrics = systemFixture(); metrics.providers = { ...metrics.providers, records: [], truncated: true };
    metrics.jobs.failure_rate = null; metrics.jobs.duration.mean_seconds = null;
    const html = renderToStaticMarkup(<OperationalMetrics metrics={metrics} error={null} loading={false} onRefresh={() => {}} />);
    expect(html).toContain("No provider observations yet");
    expect(html).toContain("Additional provider observations are not shown");
    expect(html).toContain("Unknown — no terminal sample");
    expect(measuredDuration(null)).toBe("Unknown");
    expect(knownCost("0")).toBe("£0.00");
  });
  it("strips unexpected fields from every telemetry layer", () => {
    const fixture = systemFixture();
    const result = parseSystemMetrics({ ...fixture, password: "private-secret", jobs: { ...fixture.jobs, workspace_id: "other" }, providers: { ...fixture.providers, records: fixture.providers.records.map((value) => ({ ...value, response: "raw-provider-response", api_key: "never-forward" })) } });
    expect(JSON.stringify(result)).not.toContain("private-secret");
    expect(JSON.stringify(result)).not.toContain("raw-provider-response");
    expect(JSON.stringify(result)).not.toContain("never-forward");
    expect(JSON.stringify(result)).not.toContain("workspace_id");
  });
  it("rejects unsafe labels, invalid counters and unsupported qualification claims", () => {
    const fixture = systemFixture();
    for (const change of [{ provider: "https://user:secret@example.test" }, { qualification: "VERIFIED" }, { calls: -1 }, { last_latency_seconds: NaN }]) {
      expect(() => parseSystemMetrics({ ...fixture, providers: { ...fixture.providers, records: [{ ...fixture.providers.records[0], ...change }] } })).toThrow();
    }
    expect(() => parseSystemMetrics({ ...fixture, costs: { ...fixture.costs, known_total: "NaN" } })).toThrow();
  });
});
