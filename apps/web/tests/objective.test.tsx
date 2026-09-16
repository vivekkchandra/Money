import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObjectiveDashboardCard, ObjectivePage, ObjectiveResults } from "@/components/objective";
import { Workspace } from "@/components/workspace";
import { currentOpportunities, OBJECTIVE_ORDER, OBJECTIVE_POLL_MS, objectiveQuery, observeObjective, parseObjectiveRanking, type ObjectiveLoad } from "@/lib/objective";
import { proxyBackend } from "@/lib/backend";
import { createSession, SESSION_COOKIE } from "@/lib/auth";
import { GET, POST } from "@/app/api/research/[[...path]]/route";

// Entirely synthetic contract fixtures, never live qualification evidence.
const now = Date.parse("2026-09-16T12:00:00Z");
const scenario = {
  research_id: "12345678-1234-1234-1234-123456789012", ticker: "TEST.L", company: "Synthetic test company",
  packet_hash: "a".repeat(64), snapshot_hash: "b".repeat(64), signal_design_hash: "c".repeat(64), state: "RESEARCH_CANDIDATE",
  raw_price: "125", raw_currency: "GBX", normalized_price_gbp: "1.25", conversion_method: "GBX / 100",
  assumed_capital_gbp: "200", illustrative_allocation_gbp: "100", percentage_of_assumed_capital: "50",
  modelled_downside_gbp: "10", potential_upside_gbp: "25", scenario_return_fraction: "0.25", risk_reward: "2.5",
  round_trip_cost_gbp: "1", horizon_days: 10, issued_at: "2026-09-16T11:00:00Z", valid_until: "2026-09-16T13:00:00Z",
  evidence_independence: "MEDIUM", qlib_rank: 1, qlib_universe_size: 20, spread_bps: "10", gap_to_stretch_profit_gbp: "975",
  calibration: "UNCALIBRATED", probability: null, expected_payoff_gbp: null, evidence_ids: ["fixture-evidence-1"],
};
const report = {
  version: "qualified-scenarios-v1", objective: { starting_capital: "200", target_profit: "1000", target_end_value: "1200", target_return: "5.0", horizon_days: 30, can_override_risk: false },
  evaluated_at: "2026-09-16T12:00:00Z", basis: "RESEARCH_SIMULATION_NOT_ACTUAL_PORTFOLIO", coverage: "PAGINATED_WORKSPACE_PUBLICATIONS",
  examined: 20, offset: 0, has_more: true, opportunities: [scenario], order: OBJECTIVE_ORDER,
  conclusion: "NO_QUALIFIED_OPPORTUNITY_CURRENTLY_SUPPORTS_THE_STRETCH_OBJECTIVE", limitations: ["Synthetic test record only. Alternatives are not a portfolio."], hash: "d".repeat(64),
};
afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("objective scenario contracts", () => {
  it("preserves bounded safe fields and can validate the proxy projection again", () => {
    const parsed = parseObjectiveRanking({ ...report, private_token: "SECRET", opportunities: [{ ...scenario, provider_secret: "SECRET" }] });
    expect(parseObjectiveRanking(parsed)).toEqual(parsed);
    expect(JSON.stringify(parsed)).not.toContain("SECRET");
    expect(parsed.opportunities[0].normalized_price_gbp).toBe(1.25);
  });
  it.each([{ raw_currency: "USD" }, { ticker: "DEMO.L" }, { calibration: "HIGH" }, { probability: 0.9 }, { state: "STRONG_RESEARCH_CANDIDATE" }, { illustrative_allocation_gbp: "201" }, { normalized_price_gbp: "125" }, { valid_until: scenario.issued_at }, { risk_reward: "NaN" }, { qlib_rank: 21 }])("rejects unsafe scenario %j", change => {
    expect(() => parseObjectiveRanking({ ...report, opportunities: [{ ...scenario, ...change }] })).toThrow();
  });
  it("rejects mutable objectives, unbounded pages, duplicate records and changed ranking rules", () => {
    for (const value of [
      { ...report, objective: { ...report.objective, can_override_risk: true } },
      { ...report, objective: { ...report.objective, starting_capital: "500" } },
      { ...report, opportunities: [scenario, scenario] },
      { ...report, order: ["Highest profit first"] },
      { ...report, examined: 21 },
      { ...report, opportunities: Array.from({ length: 21 }, () => scenario) },
    ]) expect(() => parseObjectiveRanking(value)).toThrow();
  });
  it.each(["limit=21", "offset=-1", "offset=10001", "offset=0&offset=20", "limit=20&limit=20", "limit=", "ticker=TEST.L", "offset=1e2"])("rejects unsafe query %s", query => {
    expect(objectiveQuery(new URLSearchParams(query))).toBeNull();
  });
  it("uses explicit bounded page defaults", () => {
    expect(objectiveQuery(new URLSearchParams())?.toString()).toBe("limit=20&offset=0");
    expect(objectiveQuery(new URLSearchParams("limit=1&offset=10000"))?.get("offset")).toBe("10000");
  });
});

describe("objective polling and freshness", () => {
  it("polls without overlapping requests and removes a previously visible report after an error", async () => {
    vi.useFakeTimers();
    const states: ObjectiveLoad[] = [];
    const load = vi.fn().mockResolvedValueOnce(report).mockRejectedValueOnce(new Error("Research service unavailable"));
    const stop = observeObjective(0, load, state => states.push(state));
    await vi.advanceTimersByTimeAsync(0);
    expect(states.at(-1)?.report?.opportunities).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(OBJECTIVE_POLL_MS);
    expect(load).toHaveBeenCalledTimes(2);
    expect(states.at(-1)).toEqual({ report: null, error: "Research service unavailable", loading: false });
    stop();
    await vi.advanceTimersByTimeAsync(OBJECTIVE_POLL_MS * 2);
    expect(load).toHaveBeenCalledTimes(2);
  });
  it("aborts and ignores a delayed response when switching pages or leaving the workspace", async () => {
    let resolve: (value: unknown) => void = () => {};
    let signal: AbortSignal | undefined;
    const states: ObjectiveLoad[] = [];
    const stop = observeObjective(0, (_, value) => { signal = value; return new Promise(done => { resolve = done; }); }, state => states.push(state));
    stop(); resolve(report); await Promise.resolve(); await Promise.resolve();
    expect(signal?.aborted).toBe(true);
    expect(states).toHaveLength(1);
  });
  it("retry can recover, and mismatched pagination cannot display another page", async () => {
    const states: ObjectiveLoad[] = [];
    let stop = observeObjective(20, async () => report, state => states.push(state));
    await Promise.resolve(); await Promise.resolve();
    expect(states.at(-1)?.report).toBeNull();
    expect(states.at(-1)?.error).toContain("different page");
    stop(); stop = observeObjective(20, async () => ({ ...report, offset: 20 }), state => states.push(state));
    await Promise.resolve(); await Promise.resolve();
    expect(states.at(-1)?.report?.offset).toBe(20); stop();
  });
  it("expires at the exact boundary and never retains a reaches-objective claim after expiry", () => {
    const parsed = parseObjectiveRanking({ ...report, opportunities: [{ ...scenario, gap_to_stretch_profit_gbp: 0 }] });
    expect(currentOpportunities(parsed, now)).toHaveLength(1);
    const expires = Date.parse(scenario.valid_until);
    expect(currentOpportunities(parsed, expires)).toEqual([]);
    const html = renderToStaticMarkup(<ObjectiveResults report={parsed} now={expires} />);
    expect(html).toContain("1 expired scenario is");
    expect(html).toContain("No currently qualified scenarios");
    expect(html).not.toContain("A modelled scenario reaches");
    expect(html).not.toContain("Potential net upside");
  });
});

describe("objective presentation", () => {
  it("shows fixed arithmetic without inventing progress or a countdown", () => {
    const html = renderToStaticMarkup(<ObjectivePage />);
    for (const value of ["£200", "£1,000", "£1,200", "+500%", "30 days", "aspirational", "Alternatives, not a portfolio", "Checking qualified publications"]) expect(html).toContain(value);
    expect(html).not.toContain('role="progressbar"');
  });
  it("links the dashboard and both workspace navigation modes", () => {
    expect(renderToStaticMarkup(<ObjectiveDashboardCard />)).toContain('href="/objective"');
    for (const commercial of [false, true]) expect(renderToStaticMarkup(<Workspace view={["objective"]} commercial={commercial} />)).toContain("£200 / 30-Day Objective");
  });
  it("shows net scenarios, raw currency, independence and immutable source links", () => {
    const html = renderToStaticMarkup(<ObjectiveResults report={parseObjectiveRanking(report)} now={now} />);
    for (const value of ["UNCALIBRATED", "£25.00", "£10.00", "£975.00", "125 GBX", "£1.25", "MEDIUM", "20 publications examined"]) expect(html).toContain(value);
    expect(html).toContain(`/research/${scenario.research_id}/decision`);
    expect(html).toContain(`/research/${scenario.research_id}/evidence`);
    expect(html).not.toMatch(/BUY NOW|SELL NOW|87% confidence/);
  });
  it("escapes company/evidence text and admits an empty result without fabrication", () => {
    const html = renderToStaticMarkup(<ObjectiveResults report={parseObjectiveRanking({ ...report, opportunities: [{ ...scenario, company: "<script>bad()</script>" }] })} now={now} />);
    expect(html).toContain("&lt;script&gt;"); expect(html).not.toContain("<script>");
    expect(renderToStaticMarkup(<ObjectiveResults report={parseObjectiveRanking({ ...report, opportunities: [], examined: 0 })} now={now} />)).toContain("No currently qualified scenarios");
  });
});

describe("authenticated objective proxy", () => {
  function request(query = "", method = "GET") {
    vi.stubEnv("MONEY_ENV", "test"); vi.stubEnv("MONEY_AUTH_MODE", "private");
    vi.stubEnv("MONEY_WEB_PASSWORD", "long-safe-test-password"); vi.stubEnv("SESSION_SECRET", "test-session-secret-longer-than-thirty-two");
    vi.stubEnv("RESEARCH_API_TOKEN", "server-only-test-token-longer-than-thirty-two"); vi.stubEnv("RESEARCH_API_URL", "http://localhost:8000");
    return new Request(`http://localhost:3000/api/research/objective${query}`, { method, headers: { Cookie: `${SESSION_COOKIE}=${createSession()}`, Origin: "http://localhost:3000" } });
  }
  function backend(status = 200, data: unknown = report) {
    const fetch = vi.fn(async (url: string | URL) => new URL(url).pathname === "/internal/auth/sessions/validate" ? Response.json({ valid: true }) : Response.json(data, { status }));
    vi.stubGlobal("fetch", fetch); return fetch;
  }
  it("routes and safely projects a valid bounded page", async () => {
    const req = request("?limit=20&offset=0"), fetch = backend();
    const response = await GET(req, { params: Promise.resolve({ path: ["objective"] }) });
    expect(response.status).toBe(200);
    expect(parseObjectiveRanking(await response.json()).opportunities).toHaveLength(1);
    expect(fetch.mock.calls.at(-1)?.[0].toString()).toBe("http://localhost:8000/research/objective?limit=20&offset=0");
  });
  it("rejects unknown parameters, writes and unauthenticated access before fetching", async () => {
    const req = request("?target=2000"), fetch = backend();
    expect((await proxyBackend(req, "/research/objective")).status).toBe(400);
    expect((await POST(request("", "POST"), { params: Promise.resolve({ path: ["objective"] }) })).status).toBe(405);
    expect((await proxyBackend(new Request(req.url), "/research/objective")).status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("explains live-only gating and refuses malformed downstream scenarios", async () => {
    const req = request(); backend(409, { code: "LIVE_RESEARCH_REQUIRED", secret: "never expose" });
    const gated = await proxyBackend(req, "/research/objective");
    expect(gated.status).toBe(409); expect((await gated.json()).code).toBe("LIVE_RESEARCH_REQUIRED");
    backend(200, { ...report, opportunities: [{ ...scenario, raw_currency: "USD" }] });
    expect((await proxyBackend(req, "/research/objective")).status).toBe(503);
  });
});
