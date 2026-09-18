import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LocalResearchArtifactViewer, ResearchTestingSummary } from "@/components/research-testing";
import { ENRICHMENT_STATES, parseResearchTesting, RESEARCH_STATE_LABELS } from "@/lib/research-admission";
import { parseUniverse } from "@/lib/universe";

const fixture = {
  purpose: "RESEARCH_TESTING", status: "RESEARCH RUN BLOCKED", state: "RESEARCH_ELIGIBLE",
  research_eligible: 2, production_ready: false, qlib_enabled: false, lean_mandatory: true, manifest_sha256: null,
  summary: { raw_instruments: 10, gbp_gbx_stocks: 3, research_identity_conflicts: 1, enrichment_coverage: { eodhd_market_data: { AVAILABLE: 1, UNAVAILABLE: 2 }, companies_house: { ACCESS_DENIED: 3 }, eodhd_fundamentals: { NOT_CONFIGURED: 3 }, official_disclosures: { STALE: 3 } } },
  results: [{ trading212_id: "NICLl_EQ", ethical_status: "UNKNOWN", first_pass: { complete: false, reports: [] }, lean: { executed: false } }],
};

describe("separate local research/testing admission display", () => {
  it("allows absent/denied enrichment and ethical UNKNOWN without implying production approval", () => {
    const result = parseResearchTesting(fixture);
    expect(result.state).toBe("RESEARCH_ELIGIBLE");
    expect(result.researchEligible).toBe(2);
    expect(result.instruments[0].ethicalStatus).toBe("UNKNOWN");
    const html = renderToStaticMarkup(<ResearchTestingSummary result={result} />);
    expect(html).toContain("Research eligible — not production approved");
    expect(html).toContain("ACCESS DENIED");
    expect(html).toContain("Missing fundamentals, Companies House or ethical screening do not block");
    expect(html).toContain("No orders or release approval");
    expect(html).toContain("Qlib disabled");
    expect(html).toContain("LEAN execution remains mandatory");
    expect(html).not.toContain("QUALIFIED");
  });
  it.each(["PASS", "FAIL", "UNKNOWN", "NOT_SCREENED"])("keeps ethics %s as an optional annotation", ethical_status => {
    const result = parseResearchTesting({ ...fixture, results: [{ ...fixture.results[0], ethical_status }] });
    expect(result.state).toBe("RESEARCH_ELIGIBLE");
    expect(result.instruments[0].ethicalStatus).toBe(ethical_status);
  });
  it("keeps unknown counts unknown instead of manufacturing a zero", () => {
    const result = parseResearchTesting({ ...fixture, summary: {}, research_eligible: 0, state: "DISCOVERED", results: [] });
    expect(result.rawInstruments).toBeNull();
    expect(result.gbpGbxStocks).toBeNull();
    const html = renderToStaticMarkup(<ResearchTestingSummary result={result} />);
    expect(html).toContain("Not recorded");
    expect(html).toContain("No independent agent runs are recorded");
  });
  it("projects only counts and statuses, excluding raw data, reports and credentials", () => {
    const result = parseResearchTesting({ ...fixture, provider_token: "DO_NOT_DISPLAY_SENTINEL", results: [{ ...fixture.results[0], raw_html: "DO_NOT_DISPLAY_SENTINEL", first_pass: { complete: false, reports: [{ firm: "tradingagents", conclusion: "DO_NOT_DISPLAY_SENTINEL" }] } }] });
    expect(JSON.stringify(result)).not.toContain("DO_NOT_DISPLAY_SENTINEL");
    expect(renderToStaticMarkup(<ResearchTestingSummary result={result} />)).not.toContain("DO_NOT_DISPLAY_SENTINEL");
  });
  it.each([
    { purpose: "PRODUCTION_QUALIFICATION" }, { production_ready: true }, { qlib_enabled: true },
    { lean_mandatory: false }, { manifest_sha256: "non-null" }, { state: "QUALIFIED" },
    { status: "SUCCESS" }, { research_eligible: -1 }, { research_eligible: 4 },
    { state: "LEAN_VALIDATED", status: "RESEARCH RUN COMPLETE" },
  ])("rejects unsafe or inconsistent result boundaries %j", update => {
    expect(() => parseResearchTesting({ ...fixture, ...update })).toThrow();
  });
  it("does not loosen the reviewed production universe parser", () => {
    expect(() => parseUniverse(fixture)).toThrow();
  });
  it("requires actual locked first-pass reports before showing researched or backtest-ready", () => {
    const first_pass = { complete: true, reports: [{ firm: "tradingagents" }, { firm: "ai_hedge_fund" }] };
    expect(parseResearchTesting({ ...fixture, state: "RESEARCHED", results: [{ ...fixture.results[0], first_pass }] }).instruments[0].state).toBe("RESEARCHED");
    expect(parseResearchTesting({ ...fixture, state: "BACKTEST_READY", results: [{ ...fixture.results[0], first_pass, lean: { state: "BACKTEST_READY", execution_performed: false } }] }).instruments[0].state).toBe("BACKTEST_READY");
    expect(() => parseResearchTesting({ ...fixture, state: "RESEARCHED", results: [{ ...fixture.results[0], first_pass: { complete: true, reports: [{ firm: "tradingagents" }] } }] })).toThrow();
  });
  it("shows LEAN validation only from explicit execution and two independent reports", () => {
    const complete = { ...fixture, state: "LEAN_VALIDATED", status: "RESEARCH RUN COMPLETE", results: [{ ...fixture.results[0], first_pass: { complete: true, reports: [{ firm: "tradingagents" }, { firm: "ai_hedge_fund" }] }, lean: { state: "LEAN_VALIDATED", execution_performed: true } }] };
    expect(parseResearchTesting(complete).instruments[0].state).toBe("LEAN_VALIDATED");
    expect(() => parseResearchTesting({ ...complete, results: [{ ...complete.results[0], lean: { state: "LEAN_VALIDATED", execution_performed: false } }] })).toThrow();
    expect(RESEARCH_STATE_LABELS.LEAN_VALIDATED).toContain("not release or trading approval");
  });
  it("accepts the runner wrapper when valid backtest inputs await the LEAN runtime", () => {
    const result = parseResearchTesting({
      ...fixture,
      state: "BACKTEST_READY",
      results: [{
        trading212_id: "NICLl_EQ", state: "BACKTEST_READY", snapshot_artifact: ["fixture-hash", "fixture-path"],
        first_pass: { complete: true, state: "FIRST_PASS_LOCKED", reports: [{ firm: "tradingagents" }, { firm: "ai_hedge_fund" }] },
        lean: { state: "BLOCKED", execution_performed: false, readiness: { state: "BACKTEST_READY", blockers: [] }, blockers: ["PINNED_LEAN_RUNTIME_IMAGE_REQUIRED"] },
      }],
    });
    expect(result.instruments[0]).toMatchObject({ state: "BACKTEST_READY", leanExecuted: false });
    expect(renderToStaticMarkup(<ResearchTestingSummary result={result} />)).toContain("LEAN execution still required");
  });
  it("keeps the aggregate at the least advanced selected candidate", () => {
    const completed = { trading212_id: "FIRSTl_EQ", state: "LEAN_VALIDATED", first_pass: { complete: true, reports: [{ firm: "tradingagents" }, { firm: "ai_hedge_fund" }] }, lean: { state: "LEAN_VALIDATED", execution_performed: true, readiness: { state: "BACKTEST_READY" } } };
    const partial = { ...fixture, results: [completed, { ...fixture.results[0], state: "RESEARCH_ELIGIBLE" }] };
    expect(parseResearchTesting(partial).state).toBe("RESEARCH_ELIGIBLE");
    expect(() => parseResearchTesting({ ...partial, status: "RESEARCH RUN COMPLETE", state: "LEAN_VALIDATED" })).toThrow();
    expect(() => parseResearchTesting({ ...partial, state: "RESEARCHED" })).toThrow();
  });
  it.each([
    { state: "LEAN_VALIDATED", execution_performed: true, readiness: { state: "BLOCKED" } },
    { state: "BLOCKED", execution_performed: false, readiness: { state: "UNKNOWN" } },
  ])("rejects inconsistent or unknown readiness %j", lean => {
    expect(() => parseResearchTesting({ ...fixture, state: "RESEARCHED", results: [{ ...fixture.results[0], first_pass: { complete: true, reports: [{ firm: "tradingagents" }, { firm: "ai_hedge_fund" }] }, lean }] })).toThrow();
  });
  it("checks explicit row states against stage evidence and legacy state aliases", () => {
    expect(() => parseResearchTesting({ ...fixture, results: [{ ...fixture.results[0], state: "RESEARCHED" }] })).toThrow();
    expect(() => parseResearchTesting({ ...fixture, results: [{ ...fixture.results[0], state: "RESEARCH_ELIGIBLE", research_state: "RESEARCHED" }] })).toThrow();
    expect(() => parseResearchTesting({ ...fixture, results: [{ ...fixture.results[0], state: "MAYBE" }] })).toThrow();
  });
  it("accepts every known optional enrichment status and rejects unknown statuses", () => {
    for (const status of ENRICHMENT_STATES) expect(parseResearchTesting({ ...fixture, summary: { ...fixture.summary, enrichment_coverage: { news: { [status]: 3 } } } }).enrichment[0].counts[status]).toBe(3);
    expect(() => parseResearchTesting({ ...fixture, summary: { ...fixture.summary, enrichment_coverage: { news: { APPROVED: 3 } } } })).toThrow();
  });
  it("offers only a local file viewer, not an invented server route or upload", () => {
    const html = renderToStaticMarkup(<LocalResearchArtifactViewer />);
    expect(html).toContain("research-testing-result.json");
    expect(html).toContain("not uploaded");
    expect(html).toContain('type="file"');
    expect(html).not.toContain("/api/");
    expect(html).not.toContain("<form");
  });
});
